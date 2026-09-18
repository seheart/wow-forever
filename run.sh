#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
[ -d venv ] || python -m venv venv
source venv/bin/activate
pip install -q -r requirements.txt
python app.py
