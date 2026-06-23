"""
Download pipeline: HLS playlist -> AES decrypt -> ffmpeg remux -> .mp4

The single source of truth used by both CLI and GUI. The GUI passes a
`progress_cb(ttid, phase, current, total, message, file)` callback to get
real-time updates; the CLI passes None and gets stdout-based reporting.

Pipeline:
  1. GET  /api/fetchvideo?ttid=&token=         -> master HLS playlist
  2. Pick variant (BANDWIDTH match), GET variant playlist
  3. GET  /api/fetchvideo/getVideoKey?ttid=&keyid= -> 18 bytes; AES key = wrapped[2:][::-1]
  4. GET each /api/fetchvideo?ts=<encoded-segment-url> -> AES-128-CBC encrypted .ts
  5. AES-128-CBC decrypt with IV=0..0 + PKCS7 unpadding
  6. Concatenate -> ffmpeg remux to .mp4 (faststart)
"""

from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .api import MAX_WORKERS
from .crypto import aes_cbc_decrypt, aes_key_from_response


# ---------- Domain ----------
@dataclass
class Lecture:
    ttid: int
    video_id: int = 0
    title: str = ""
    subject: str = ""
    professor: str = ""
    start_time: str = ""
    duration_s: int = 0


# ---------- Per-folder "already done" tracker ----------
class Manifest:
    """
    .manifest.json inside the output dir. Records which ttids we've successfully
    downloaded so re-runs skip them. Public so cli/gui can query is_done().
    """

    def __init__(self, out_dir: Path):
        self.out_dir = out_dir
        self.path = out_dir / ".manifest.json"
        self.data: dict = {}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text())
            except Exception:
                self.data = {}

    def save(self):
        self.path.write_text(json.dumps(self.data, indent=2, sort_keys=True))

    def is_done(self, ttid: int) -> bool:
        return self.data.get(str(ttid), {}).get("status") == "done"

    def mark_done(self, ttid: int, file: Path, title: str, subject: str):
        sha = hashlib.sha256(file.read_bytes()).hexdigest()[:16]
        self.data[str(ttid)] = {
            "status": "done", "file": str(file), "sha": sha,
            "title": title, "subject": subject,
        }
        self.save()


# ---------- Playlist parsing ----------
def pick_variant(master: str, prefer: str = "") -> tuple[str, int]:
    """Pick the variant URL matching `prefer` bandwidth, else highest."""
    lines = [l.strip() for l in master.splitlines() if l.strip()]
    pairs = []
    for i, line in enumerate(lines):
        m = re.match(r"#EXT-X-STREAM-INF:.*BANDWIDTH=(\d+)", line)
        if m and i + 1 < len(lines):
            pairs.append((int(m.group(1)), lines[i + 1]))
    if not pairs:
        raise RuntimeError("no variants in master playlist")
    for bw, url in pairs:
        if str(bw) == prefer:
            return url, bw
    best = max(pairs, key=lambda x: x[0])
    return best[1], best[0]


def parse_segments(variant: str) -> list[tuple[float, str]]:
    """[(duration_seconds, segment_url), ...]"""
    out = []
    dur = None
    for line in variant.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#EXTINF:"):
            m = re.match(r"#EXTINF:([\d.]+)", line)
            dur = float(m.group(1)) if m else 0.0
        elif not line.startswith("#"):
            out.append((dur or 0.0, line))
            dur = None
    return out


def safe_filename(s: str, max_len: int = 120) -> str:
    """filesystem-safe slug: replace non-word chars with underscores."""
    s = re.sub(r"[^\w\s.-]", "_", s)
    s = re.sub(r"\s+", "_", s).strip("_")
    return s[:max_len] or "lecture"


# ---------- The main download function ----------
# Callback signature:
#   progress_cb(ttid: int, phase: str, current: int, total: int,
#               message: str, file_path: Optional[str])
# phases: "starting", "playlist", "key", "decrypting", "muxing", "done", "error"
ProgressCB = Optional[Callable[[int, str, int, int, str, Optional[str]], None]]


def _enrich_metadata(client, lec: Lecture) -> None:
    """If we were given just a ttid, look it up in subjects to fill in title/subject/etc."""
    if not (lec.title.startswith("ttid_") and lec.subject == "?"):
        return
    try:
        for s in client.list_subjects():
            try:
                lecs = client.list_lectures(s["subjectId"], s["sessionId"])
            except Exception:
                continue
            for L in lecs:
                if L["ttid"] == lec.ttid:
                    lec.title = L.get("topic") or f"lecture_{lec.ttid}"
                    lec.subject = s.get("subjectName", "?")
                    lec.professor = L.get("professorName", "?")
                    lec.start_time = L.get("startTime", "")
                    lec.duration_s = int(L.get("actualDuration") or 0)
                    return
    except Exception:
        pass


def download_lecture(
    client,
    lec: Lecture,
    manifest: Manifest,
    prefer_bandwidth: str = "",
    progress_cb: ProgressCB = None,
    quiet: bool = False,
) -> Optional[Path]:
    """
    Download one lecture end-to-end.

    Returns the final Path on success, None on failure (manifest NOT updated).
    Re-uses cached download if `manifest.is_done(ttid)` and the file still exists.
    """
    def emit(phase, cur, tot, msg, fp):
        if progress_cb:
            progress_cb(lec.ttid, phase, cur, tot, msg, fp)
        elif not quiet:
            print(f"  [{phase}] {msg}", flush=True)

    # Already done?
    if manifest.is_done(lec.ttid):
        prev = Path(manifest.data[str(lec.ttid)]["file"])
        if prev.exists():
            emit("done", 1, 1, f"already downloaded: {prev.name}", str(prev))
            return prev

    _enrich_metadata(client, lec)

    if not quiet:
        print(f"\n=== ttid={lec.ttid} :: {lec.title} ===", flush=True)

    work = manifest.out_dir / f".work_{lec.ttid}"
    work.mkdir(parents=True, exist_ok=True)

    try:
        # 1. master + variant playlist
        emit("playlist", 1, 4, "fetching master playlist", None)
        master = client.get_master_playlist(lec.ttid)
        variant_url, bw = pick_variant(master, prefer=prefer_bandwidth)
        variant = client.get_variant_playlist(variant_url)
        segments = parse_segments(variant)
        total_segs = len(segments)
        if not quiet:
            print(f"  variant: BANDWIDTH={bw}", flush=True)
            print(f"  {total_segs} segments, ~{sum(d for d, _ in segments):.0f}s", flush=True)

        # 2. AES key
        emit("key", 2, 4, f"fetching AES key ({total_segs} segments)", None)
        wrapped = client.get_key(lec.ttid)
        key = aes_key_from_response(wrapped)
        if not quiet:
            print(f"  AES key: {key.hex()}", flush=True)

        # 3. download + decrypt each segment in parallel
        emit("decrypting", 0, total_segs, "downloading + decrypting", None)
        seg_dir = work / "plain"
        seg_dir.mkdir(exist_ok=True)

        def fetch_and_decrypt(idx_dur_url):
            idx, (_, url) = idx_dur_url
            target = seg_dir / f"{idx:05d}.ts"
            if target.exists() and target.stat().st_size > 0:
                return idx, target
            enc = client.fetch_segment(url)
            plain = aes_cbc_decrypt(enc, key)
            target.write_bytes(plain)
            return idx, target

        todo = list(enumerate(segments))
        done_count = 0
        t0 = time.time()
        with cf.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futs = [pool.submit(fetch_and_decrypt, t) for t in todo]
            for fut in cf.as_completed(futs):
                fut.result()
                done_count += 1
                if done_count % 5 == 0 or done_count == total_segs:
                    rate = done_count / max(1e-6, time.time() - t0)
                    emit("decrypting", done_count, total_segs,
                         f"decrypted {done_count}/{total_segs} ({rate:.1f}/s)", None)

        # 4. concatenate + 5. ffmpeg remux
        emit("muxing", total_segs, total_segs, "concatenating + remuxing to mp4", None)
        concat_ts = work / "concat.ts"
        with concat_ts.open("wb") as out:
            for idx in range(total_segs):
                out.write((seg_dir / f"{idx:05d}.ts").read_bytes())

        final_name = safe_filename(
            f"{lec.start_time[:10]}_{lec.subject}_{lec.title}_{lec.ttid}.mp4"
        )
        final_path = manifest.out_dir / final_name
        i = 1
        while final_path.exists():
            final_name = safe_filename(
                f"{lec.start_time[:10]}_{lec.subject}_{lec.title}_{lec.ttid}_{i}.mp4"
            )
            final_path = manifest.out_dir / final_name
            i += 1

        if shutil.which("ffmpeg"):
            tmp = work / "out.mp4"
            r = subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", str(concat_ts),
                 "-c", "copy", "-movflags", "+faststart", str(tmp)],
                capture_output=True, timeout=300)
            if r.returncode != 0:
                raise RuntimeError(f"ffmpeg failed: {r.stderr.decode()[:200]}")
            shutil.move(str(tmp), str(final_path))
        else:
            if not quiet:
                print("  ffmpeg missing; saving as .ts")
            final_path = final_path.with_suffix(".ts")
            shutil.move(str(concat_ts), str(final_path))

        manifest.mark_done(lec.ttid, final_path, lec.title, lec.subject)
        if not quiet:
            print(f"  DONE -> {final_path.name}  ({final_path.stat().st_size:,} bytes)", flush=True)
        emit("done", total_segs, total_segs, f"saved {final_path.name}", str(final_path))
        return final_path

    except Exception as e:
        emit("error", 0, 0, f"{type(e).__name__}: {e}", None)
        if not quiet:
            print(f"  FAILED ttid={lec.ttid}: {type(e).__name__}: {e}", flush=True)
        return None
    finally:
        try:
            shutil.rmtree(work)
        except Exception:
            pass