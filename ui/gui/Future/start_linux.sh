#!/usr/bin/env bash
set -e
cd -- "$(dirname -- "$0")"
if [ ! -x ../.venv/bin/python ]; then
  echo 'Create gui/.venv and install requirements first. See gui/README_KO.md.' >&2
  exit 1
fi
exec ../.venv/bin/python run.py
