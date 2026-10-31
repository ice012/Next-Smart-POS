#!/usr/bin/env bash
# Start the Next-Smart-Pos backend on Render.
#
# Render calls this via the `startCommand` in render.yaml:
#     bash backend/start.sh
#
# It must:
#   - exit non-zero if the app fails to import (so a broken deploy is obvious)
#   - exec gunicorn in the foreground (Render manages the process lifecycle)
set -euo pipefail

cd "$(dirname "$0")"

# Fail fast if the app cannot even be imported - a broken dependency or a
# typo in a route module would otherwise surface only on the first request.
python -c "from run import create_app; create_app()" >/dev/null 2>&1 || {
    echo "ERROR: app factory failed to import" >&2
    exit 1
}

exec gunicorn --config gunicorn.conf.py run:app