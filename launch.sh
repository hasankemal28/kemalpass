#!/bin/sh
set -eu
cd /opt/facepass
if [ -x /opt/facepass/venv/bin/python ]; then
  exec /opt/facepass/venv/bin/python app.py
fi
exec /usr/bin/python3 app.py
