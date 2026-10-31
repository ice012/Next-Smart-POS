# Next-Smart-Pos backend container image.
#
# Build (from repo root):
#   docker build -t nsp-backend -f backend/Dockerfile .
#
# Run locally:
#   docker run --rm -p 8000:8000 -e DATABASE_URL=sqlite:////data/app.db \
#     -v "$(pwd)/backend/uploads:/uploads" nsp-backend
#
# The image is framework-only: it installs Python deps, copies the app, and
# runs gunicorn. Render uses this image when you deploy a "Docker Web Service"
# instead of the Python blueprint in render.yaml.

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Runtime deps for psycopg2 and reportlab.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first so the layer is cached unless requirements change.
COPY backend/requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt gunicorn==23.0.0

# Copy the application.
COPY backend /app

# Non-root user - the app only needs write access to the uploads folder.
RUN addgroup --system nsp && adduser --system --group nsp \
    && chown -R nsp:nsp /app
USER nsp

EXPOSE 8000

# $PORT is injected by Render; fall back to 8000 for local runs.
CMD ["sh", "-c", "gunicorn --config gunicorn.conf.py run:app"]