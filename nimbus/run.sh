#!/usr/bin/env bash
# Nimbus launcher. Creates a venv if needed and starts the server.
set -e
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "Creating virtual environment…"
  python3 -m venv .venv
fi
source .venv/bin/activate
pip install -q -r requirements.txt

echo "Starting Nimbus on http://0.0.0.0:8000"
python backend/main.py
