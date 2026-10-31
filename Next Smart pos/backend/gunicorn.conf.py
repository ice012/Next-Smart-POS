"""Gunicorn configuration for Next-Smart-Pos backend.

Used by render.yaml (startCommand) and by the Dockerfile. All tunables are
read from environment variables so the same config works everywhere.
"""
import os
import multiprocessing

# Bind to the port Render assigns; $PORT is injected at runtime.
bind = "0.0.0.0:" + os.environ.get("PORT", "8000")

# Worker model - sync workers keep memory flat and work fine with the
# modest traffic of a single-POS install.
worker_class = "sync"
workers = int(os.environ.get("WORKERS", "4"))
worker_connections = 1000
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "120"))
graceful_timeout = int(os.environ.get("GUNICORN_GRACEFUL_TIMEOUT", "30"))
keepalive = 2

# Logging - JSON-ish lines are easy to grep in Render's log tail.
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info")

# Don't daemonise inside a container.
daemon = False
tmp_upload_dir = os.environ.get("TMP_UPLOAD_DIR", "/opt/render/project/backend/tmp")

# Preload the app so the first request is fast and the WSGI app shares the
# module-level caches (e.g. the M-Pesa token cache).
preload_app = False