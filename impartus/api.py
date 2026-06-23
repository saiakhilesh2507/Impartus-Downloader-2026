"""
Impartus HTTP API client + session-token cache.

Auth flow:
  1. POST /api/auth/signin            -> JWT bearer token
  2. All subsequent calls include `Authorization: Bearer <token>`
  3. Cache the token in <config_dir>/session.json (password NEVER stored)

Cache location:
  - Windows : %APPDATA%/impartus-dl/session.json
  - POSIX   : $XDG_CONFIG_HOME/impartus-dl or ~/.config/impartus-dl
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

# ---------- Server config ----------
BASE_URL = "https://a.impartus.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# HLS variant bandwidths
BANDWIDTH_480 = "109040"
BANDWIDTH_720 = "343964"
DEFAULT_QUALITY = BANDWIDTH_720

MAX_WORKERS = 6
SEGMENT_RETRIES = 3
REQUEST_TIMEOUT = 30


# ---------- Session cache ----------
def config_dir() -> Path:
    """Per-user config directory for cached session + future settings."""
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home())
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return base / "impartus-dl"


def cache_file() -> Path:
    return config_dir() / "session.json"


def load_session() -> dict:
    """Returns {username, token} from cache, or {} if missing/unparseable."""
    try:
        return json.loads(cache_file().read_text())
    except Exception:
        return {}


def save_session(username: str, token: str) -> None:
    """Persist username + JWT. Password NEVER saved."""
    try:
        config_dir().mkdir(parents=True, exist_ok=True)
        cache_file().write_text(json.dumps({"username": username, "token": token}))
        try:
            os.chmod(cache_file(), 0o600)
        except Exception:
            pass  # Windows ignores chmod; %APPDATA% is per-user anyway
    except Exception as e:
        print(f"  (warning: couldn't save session cache: {e})", file=sys.stderr)


def clear_session() -> None:
    """Delete the cached token. Used by --logout / GUI logout button."""
    cf = cache_file()
    if cf.exists():
        cf.unlink()
        print(f"Cleared cached session at {cf}")
    else:
        print("No cached session.")


# ---------- HTTP client ----------
class Impartus:
    """Thin wrapper around the Impartus REST API."""

    def __init__(self, base: str = BASE_URL):
        self.base = base.rstrip("/")
        self.token: Optional[str] = None
        self.headers = {"User-Agent": UA, "Accept": "*/*"}

    def _req(self, method: str, path: str, *, auth: bool = True,
             body: Optional[dict] = None, params: Optional[dict] = None,
             timeout: int = REQUEST_TIMEOUT, raw: bool = False):
        """Make an HTTP request. Returns (status_code, decoded_or_raw_body)."""
        url = f"{self.base}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        h = dict(self.headers)
        if body is not None:
            h["Content-Type"] = "application/json"
        if auth and self.token:
            h["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(url, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                payload = r.read()
                return r.status, (payload if raw else payload.decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", errors="replace")

    def signin(self, username: str, password: str) -> dict:
        code, body = self._req("POST", "/api/auth/signin", auth=False,
                               body={"username": username, "password": password})
        if code != 200:
            raise RuntimeError(f"signin HTTP {code}: {body[:200]}")
        data = json.loads(body)
        if not data.get("token"):
            raise RuntimeError(f"signin response missing token: {data}")
        self.token = data["token"]
        return data

    def try_cached_token(self) -> bool:
        """Cheap API call to verify self.token is still good."""
        if not self.token:
            return False
        code, _ = self._req("GET", "/api/user/profile")
        return code == 200

    def profile(self) -> dict:
        code, body = self._req("GET", "/api/user/profile")
        if code != 200:
            raise RuntimeError(f"profile HTTP {code}: {body[:200]}")
        return json.loads(body)

    def list_subjects(self) -> list[dict]:
        code, body = self._req("GET", "/api/subjects")
        if code != 200:
            raise RuntimeError(f"subjects HTTP {code}: {body[:200]}")
        return json.loads(body)

    def list_lectures(self, subject_id: int, session_id: int) -> list[dict]:
        path = f"/api/subjects/{subject_id}/lectures/{session_id}"
        code, body = self._req("GET", path)
        if code != 200:
            raise RuntimeError(f"lectures HTTP {code}: {body[:200]}")
        return json.loads(body)

    def get_master_playlist(self, ttid: int) -> str:
        code, body = self._req("GET", "/api/fetchvideo",
                               params={"ttid": ttid, "token": self.token})
        if code == 524:
            raise RuntimeError(
                f"master playlist HTTP 524 (Cloudflare origin timeout) — "
                f"ttid={ttid} may not exist on your account, or Impartus's "
                f"backend is hanging on it. Try a different ttid, or use "
                f"`--video-id` to auto-resolve from the URL."
            )
        if code != 200:
            raise RuntimeError(f"master playlist HTTP {code}: {body[:200]}")
        return body

    def get_variant_playlist(self, variant_url: str) -> str:
        if "token=" not in variant_url:
            sep = "&" if "?" in variant_url else "?"
            variant_url = f"{variant_url}{sep}token={urllib.parse.quote(self.token)}"
        req = urllib.request.Request(variant_url, headers=self.headers)
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
            return r.read().decode("utf-8", errors="replace")

    def get_key(self, ttid: int, keyid: int = 0) -> bytes:
        code, body = self._req("GET", "/api/fetchvideo/getVideoKey",
                               params={"ttid": ttid, "keyid": keyid}, raw=True)
        if code != 200:
            raise RuntimeError(f"key HTTP {code}: {body[:200] if isinstance(body,str) else body!r}")
        return body

    def fetch_segment(self, seg_url: str) -> bytes:
        if "token=" not in seg_url:
            sep = "&" if "?" in seg_url else "?"
            seg_url = f"{seg_url}{sep}token={urllib.parse.quote(self.token)}"
        last = None
        for attempt in range(SEGMENT_RETRIES):
            try:
                req = urllib.request.Request(seg_url, headers=self.headers)
                with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
                    return r.read()
            except Exception as e:
                last = e
                time.sleep(1 + attempt)
        raise RuntimeError(f"segment failed after {SEGMENT_RETRIES} tries: {seg_url[:80]} ({last})")