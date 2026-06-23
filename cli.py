#!/usr/bin/env python3
"""
cli.py - command-line Impartus lecture downloader.

Usage:
    python cli.py --list
    python cli.py --subject "Microwave Engineering"
    python cli.py --ttid 10686464 --quality 480
    python cli.py --video-id 7129561
    python cli.py --all --out ./lectures
    python cli.py --logout
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

from impartus import (
    BANDWIDTH_480, BANDWIDTH_720, DEFAULT_QUALITY,
    Impartus, Lecture, Manifest,
    clear_session, load_session,
)
from impartus.download import download_lecture


def _login(client: Impartus, args) -> bool:
    """Try cached token first, fall back to env / prompt. Returns True on success."""
    cached = load_session()
    cached_username = cached.get("username")
    cached_token = cached.get("token")

    username = args.user or os.environ.get("IMPARTUS_USER") or cached_username
    if not username:
        username = input("Impartus username: ").strip()

    # Try cached token
    if cached_token and (not args.user or args.user == cached_username):
        client.token = cached_token
        if client.try_cached_token():
            prof = client.profile()
            print(f"(cached session) User: {prof.get('fullname')} ({prof.get('email')})")
            print(f"  session={prof.get('sessionName')} dept={prof.get('departmentName')}")
            return True
        print("(cached token expired or rejected - need to re-login)")
        client.token = None

    password = os.environ.get("IMPARTUS_PASS") or getpass.getpass("Impartus password: ")
    signin = client.signin(username, password)
    from impartus import save_session
    save_session(username, client.token)
    prof = client.profile()
    print(f"Signed in (userType={signin.get('userType')}).")
    print(f"User: {prof.get('fullname')} ({prof.get('email')}); "
          f"session={prof.get('sessionName')}; dept={prof.get('departmentName')}")
    return True


def _build_targets(client: Impartus, args) -> list[Lecture]:
    """Resolve CLI flags into a list[Lecture] ready to download."""
    targets: list[Lecture] = []

    # --ttid N N N (minimal metadata; download() will enrich)
    for raw in args.ttid or []:
        targets.append(Lecture(ttid=raw, title=f"ttid_{raw}", subject="?"))

    # --video-id N N N (search all subjects to find the matching ttid)
    if args.video_id:
        print(f"Resolving {len(args.video_id)} videoId(s) to ttid(s)...")
        subs = client.list_subjects()
        for vid in args.video_id:
            found = None
            found_subject = None
            for s in subs:
                try:
                    lecs = client.list_lectures(s["subjectId"], s["sessionId"])
                except Exception:
                    continue
                for L in lecs:
                    if L.get("videoId") == vid:
                        found = L
                        found_subject = s
                        break
                if found:
                    break
            if not found:
                print(f"  videoId={vid}: NOT FOUND in any enrolled subject", file=sys.stderr)
                continue
            print(f"  videoId={vid} -> ttid={found['ttid']}  ({found_subject.get('subjectName', '?')})")
            targets.append(Lecture(
                ttid=found["ttid"], video_id=vid,
                title=found.get("topic") or f"lecture_{vid}",
                subject=found_subject.get("subjectName", "?"),
                professor=found.get("professorName", "?"),
                start_time=found.get("startTime", ""),
                duration_s=int(found.get("actualDuration") or 0),
            ))

    # --all or --subject
    if args.all or args.subject:
        subs = client.list_subjects()
        sel = []
        for raw in args.subject or []:
            if raw.isdigit():
                sel.extend([s for s in subs if str(s["subjectId"]) == raw])
            else:
                needle = raw.lower()
                sel.extend([s for s in subs if needle in s.get("subjectName", "").lower()])
        if args.all:
            sel = subs
        # Dedup
        seen = set(); uniq = []
        for s in sel:
            if s["subjectId"] not in seen:
                seen.add(s["subjectId"]); uniq.append(s)
        sel = uniq
        if not sel:
            print("No subjects matched.", file=sys.stderr)
            return []
        for s in sel:
            print(f"\n>>> {s.get('subjectName')} (id={s['subjectId']})")
            try:
                lecs = client.list_lectures(s["subjectId"], s["sessionId"])
            except Exception as e:
                print(f"  list failed: {e}"); continue
            for lec in lecs:
                targets.append(Lecture(
                    ttid=lec["ttid"], video_id=lec.get("videoId", 0),
                    title=lec.get("topic") or f"lecture_{lec['ttid']}",
                    subject=s.get("subjectName", "?"),
                    professor=lec.get("professorName", "?"),
                    start_time=lec.get("startTime", ""),
                    duration_s=int(lec.get("actualDuration") or 0),
                ))

    return targets


def main() -> int:
    p = argparse.ArgumentParser(
        description="Download Impartus lecture recordings.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Notes:
  - The first time you run, you'll be prompted for username/password.
  - Only the bearer JWT is cached (~%APPDATA%/impartus-dl/session.json).
    Password is never written to disk.
  - --video-id takes the ID from the browser URL (/video/id/<N>); the
    tool searches your subjects to find the matching ttid.
        """,
    )
    p.add_argument("--list", action="store_true", help="List enrolled subjects and exit.")
    p.add_argument("--all", action="store_true", help="Download every lecture in every subject.")
    p.add_argument("--subject", action="append", default=[],
                   help="Subject id (int) or name substring. Repeatable.")
    p.add_argument("--ttid", type=int, action="append", default=[],
                   help="Specific lecture ttid. Repeatable.")
    p.add_argument("--video-id", type=int, action="append", default=[],
                   help="Browser-URL video id; auto-resolved to ttid. Repeatable.")
    p.add_argument("--out", default="./impartus_downloads",
                   help="Output directory (default: ./impartus_downloads).")
    p.add_argument("--quality", choices=["480", "720", "highest"], default="720")
    p.add_argument("--user", default=None, help="Username (omit to prompt or use cached).")
    p.add_argument("--logout", action="store_true", help="Forget cached username + token.")
    args = p.parse_args()

    if args.logout:
        clear_session()
        return 0

    if not (args.list or args.all or args.subject or args.ttid or args.video_id):
        p.error("specify --list, --all, --subject, --ttid, --video-id, or --logout")

    # Quality -> bandwidth
    prefer = {"480": BANDWIDTH_480, "720": BANDWIDTH_720}.get(args.quality, "")

    client = Impartus()
    if not _login(client, args):
        return 1

    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = Manifest(out_dir)

    if args.list:
        for s in client.list_subjects():
            ll = s.get("lastLecture", {}) or {}
            print(f"  id={s['subjectId']:>8}  sess={s['sessionId']:<5}  "
                  f"last_ttid={ll.get('ttid','-'):>8}  :: {s.get('subjectName')}")
        return 0

    targets = _build_targets(client, args)
    if not targets:
        print("Nothing to do.", file=sys.stderr)
        return 1

    print(f"\nQueued {len(targets)} lecture(s).")
    ok = fail = 0
    for lec in targets:
        if download_lecture(client, lec, manifest, prefer_bandwidth=prefer):
            ok += 1
        else:
            fail += 1

    print(f"\nDone. {ok} succeeded, {fail} failed.")
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    sys.exit(main())