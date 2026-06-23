#!/usr/bin/env bash
# Launch the Impartus Downloader GUI on macOS / Linux.
set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required. Install it from your package manager or python.org."
  exit 1
fi

# Install deps on first run.
if ! python3 -c "import flask, cryptography" 2>/dev/null; then
  echo "Installing requirements..."
  python3 -m pip install -r requirements.txt
fi

exec python3 gui.py "$@"
