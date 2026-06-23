# Impartus Lecture Downloader

A self-contained downloader for recordings hosted on the Impartus platform
(used by Manipal University and a few other Indian institutions). Ships as a
CLI for power users and a tiny local web GUI for one-click bulk downloads.

```
  ┌─────────────────────────────────────┐
  │  📚 Impartus Downloader             │
  │  ────────────────────────────────   │
  │  Microwave Engineering       (42)   │
  │  ────────────────────────────────   │
  │  ☐ 🎙️ 12/04  Antennas  · Prof…  ⬇  │
  │  ☐ 🎙️ 11/04  Waveguides · Prof… ✓  │
  │  ☐   🎙️ 10/04  Impedance  · …     │  ← greyed mic = no audio
  │  ────────────────────────────────   │
  │  [ Select all ]  [ ⬇ Download 2 ]  │
  └─────────────────────────────────────┘
```

## Features

- **One-shot sign-in** with username + password. The bearer JWT is cached
  locally (in `%APPDATA%/impartus-dl/session.json` on Windows,
  `~/.config/impartus-dl/session.json` elsewhere). Passwords are never
  written to disk.
- **Bulk download** by subject, by ttid, or by the browser-URL `videoId`
  (auto-resolved to ttid by searching your enrolled subjects).
- **Quality selector** (480p / 720p / highest) — matches the HLS variant
  bandwidths Impartus actually serves.
- **Local web GUI** (Flask, single-page) with:
  - Subjects sidebar grouped by session
  - Lectures grid with checkboxes + select-all / clear / undownloaded-only
  - 🎙️ mic icons showing whether each lecture has audio (matches the
    mic icon in the official Impartus UI)
  - Per-lecture progress bar polled at 1 Hz
  - "Files" panel listing what's already in the output folder
- **Resumable**: re-running the same target skips lectures already
  recorded in `.manifest.json` inside the output dir.

## Quick start

### 0. Get the code

You need the project on your machine first. Pick one:

**Clone (recommended, lets you `git pull` for updates):**
```
git clone https://github.com/saiakhilesh2507/Impartus-Downloader-2026
cd Impartus-Downloader-2026
```

**Download ZIP** (no git required):
1. Go to the repo page on GitHub
2. Click the green **Code** button → **Download ZIP**
3. Extract the ZIP somewhere (e.g. your Desktop or `C:\Tools\`)
4. `cd` into the extracted folder before running the commands below

> All the steps below assume your terminal's current directory is the
> project root (the folder that contains `cli.py` and `gui.py`).

### 1. Install Python + ffmpeg

- **Python 3.10+** — [python.org/downloads](https://www.python.org/downloads/).
  On the Windows installer, tick **"Add Python to PATH"**.
- **ffmpeg** — needed for the final `.ts` → `.mp4` remux.
  - Windows: `winget install Gyan.FFmpeg` (or download a build from
    [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) and put `ffmpeg.exe`
    on your `PATH`)
  - macOS: `brew install ffmpeg`
  - Linux: `sudo apt install ffmpeg` (or your distro's equivalent)

Verify with `python --version` and `ffmpeg -version`.

### 2. Install the Python dependencies

```
pip install -r requirements.txt
```

### 3. Launch

**Windows (easiest — double-click):**
```
run_gui.bat
```
Your browser opens at http://127.0.0.1:5050. Done.

**macOS / Linux:**
```
chmod +x run_gui.sh
./run_gui.sh
```

**Cross-platform manual launch:**
```
python gui.py
# or with a custom port / output dir:
python gui.py --port 8080 --out ./impartus_downloads
```

**CLI only (no GUI):**
```
python cli.py --list                  # list your enrolled subjects
python cli.py --subject "Microwave Engineering"   # download one subject
python cli.py --ttid 10686464         # download one specific lecture
python cli.py --all --out ./lectures  # download everything
python cli.py --logout                # forget cached token
```

### 4. Where do my downloads go?

By default, both the CLI and the GUI write to:

- **Windows:** `<project-folder>\impartus_downloads\`
- **macOS / Linux:** `<project-folder>/impartus_downloads/`

so the path is
`./impartus_downloads/` relative to where you ran the command.

To put downloads somewhere else, pass `--out <path>`:

```
python gui.py --out D:\Lectures
python cli.py --ttid 10686464 --out D:\Lectures
```

The GUI's bottom-of-page **"Downloaded files in this folder"** panel
always shows the actual filenames + sizes in your current output folder
so you can see what's there without leaving the browser. In the CLI,
each successful download prints the full output path, e.g.:

```
Saved: ./impartus_downloads/2026-04-27_Microwave_Engineering_topic_10686464.mp4 (26.4 MB)
```

Each lecture is named `<date>_<subject>_<topic>_<ttid>.mp4` so you can
spot the one you want at a glance. Re-running the same target is
safe — the file is skipped, not re-downloaded.

## How it works

Impartus serves lecture video as an AES-128-CBC encrypted HLS stream.
The unwrap is:

1. `POST /api/auth/signin` with username+password → bearer JWT.
2. `GET /api/subjects` → enrolled courses, each with `subjectId` + `sessionId`.
3. `GET /api/subjects/{sid}/lectures/{sessionId}` → per-lecture metadata
   (including `ttid`, `videoId`, `noaudio`).
4. `GET /api/fetchvideo?ttid=…&token=…` → master HLS playlist.
5. `GET /api/fetchvideo/getVideoKey?ttid=…&keyid=0` → 18-byte wrapped
   AES-128 key. The actual key is `wrapped[2:][::-1]` (the first two
   bytes are a `0x02 0x01` header).
6. `GET` each segment URL (with `&token=…` injected) → AES-128-CBC
   ciphertext. IV is 16 zero bytes. PKCS7 padding.
7. Concatenate segments → `ffmpeg -c copy -movflags +faststart` → `.mp4`.

A `<output-dir>/.manifest.json` tracks which ttids have been downloaded
so re-runs are idempotent.

The wrap/unwrap is the same scheme used by the Impartus web player.

## Security & privacy

- **No telemetry, no network calls except to `https://a.impartus.com/`.**
  The GUI only listens on `127.0.0.1` (localhost) by default — not
  reachable from other machines. Pass `--host 0.0.0.0` to expose it
  (don't do this on a public network without a reverse proxy + auth).
- **Passwords are never persisted.** Only the bearer JWT (which is valid
  for 180 days per the Impartus login flow) is stored in
  `session.json`. Clear it with `python cli.py --logout` or the
  GUI's "Sign out" button.
- **Session cache permissions**: on POSIX systems the cache file is
  `chmod 600`. On Windows the file lives under `%APPDATA%` which is
  per-user by default.

## Troubleshooting

- **`python` not found** — re-run the Python installer and tick
  "Add Python to PATH", or use `python3` instead.
- **`pip install` fails behind a college proxy** — try
  `pip install --user -r requirements.txt` or use a virtualenv.
- **`ffmpeg` not found / output is `.ts` not `.mp4`** — install ffmpeg
  and ensure it's on `PATH` (restart your terminal after installing).
- **GUI opens but login fails with a network error** — your college
  firewall may be blocking `a.impartus.com`. Try the same login in a
  browser to confirm.
- **"ttid not found in your subjects"** — the lecture isn't in any of
  your currently enrolled subjects, so it can't be downloaded.
- **Want to start over** — delete the `./impartus_downloads/.manifest.json`
  file. The actual `.mp4` files are kept.

## Project layout

```
impartus-dl/
├── impartus/                  # shared library (used by cli + gui)
│   ├── __init__.py
│   ├── api.py                 #   Impartus client + session cache
│   ├── crypto.py              #   AES key unwrap + segment decrypt
│   └── download.py            #   download_lecture pipeline + Manifest
├── cli.py                     # command-line entry point
├── gui.py                     # Flask web GUI
├── run_gui.bat                # Windows launcher (double-click)
├── run_gui.sh                 # macOS / Linux launcher
├── requirements.txt
├── LICENSE                    # MIT
└── README.md
```

## Requirements

- Python 3.10+
- `cryptography` (AES)
- `flask` (GUI only)
- `ffmpeg` on PATH (for the final `.ts` → `.mp4` remux; if missing,
  output falls back to `.ts`)

## License

MIT — see [LICENSE](LICENSE).
