"""
impartus - shared library for the Impartus lecture downloader.

Used by cli.py and gui.py. Not intended to be called directly.
"""

from .api import (
    BASE_URL, BANDWIDTH_480, BANDWIDTH_720, DEFAULT_QUALITY,
    Impartus,
    load_session, save_session, clear_session,
    config_dir, cache_file,
)
from .crypto import aes_key_from_response, aes_cbc_decrypt
from .download import Lecture, Manifest, parse_segments, pick_variant, safe_filename, download_lecture

__version__ = "1.0.0"
__all__ = [
    "BASE_URL", "BANDWIDTH_480", "BANDWIDTH_720", "DEFAULT_QUALITY",
    "Impartus",
    "load_session", "save_session", "clear_session", "config_dir", "cache_file",
    "aes_key_from_response", "aes_cbc_decrypt",
    "Lecture", "Manifest", "parse_segments", "pick_variant",
    "safe_filename", "download_lecture",
]