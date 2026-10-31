# Next-Smart-Pos — Production Deployment Guide (Render)

This repo ships a **Render blueprint** (`render.yaml` at the root) that
provisions two web services from a single git push. No manual server setup
required.

## What gets deployed

| Service      | Type      | What it runs                                       |
| ------------ | --------- | -------------------------------------------------- |
| `nsp-backend`  | Python web | Flask API behind gunicorn, migrations run on deploy |
| `nsp-frontend` | Node web   | Vite SPA built to `dist/` and served with `serve`  |

The PostgreSQL database is managed externally (instance
`dpg-dbilglgae00c73c1fpag-a`, database `smartposdb_g4mk`). Its connection
string is passed to the backend as the `DATABASE_URL` env var.

## 1. Sign up and import

1. Create a free account at [render.com](https://render.com).
2. Click **"New" → "Blueprint"** and point it at this repo.
3. Render reads `render.yaml` and shows a preview of the services it will
   create. Click **"Apply"**.

## 2. Set the secrets

Open the **Environment** tab for each service and set these values. Render
supports per-environment overrides, so you can keep a different value for
staging if you fork the blueprint.

### nsp-backend (required)

| Variable                 | How to generate                                             |
| ------------------------ | ----------------------------------------------------------- |
| `SECRET_KEY`             | `python -c "import secrets; print(secrets.token_hex(32))"`  |
| `JWT_SECRET_KEY`         | same as above, use a different value                        |
| `SUPERADMIN_API_TOKEN`   | `python -c "import secrets; print(secrets.token_hex(24))"`  |
| `MPESA_CALLBACK_TOKEN`   | any string you choose — append `?token=<same>` to the Daraja callback URL registered with Safaricom |
| `CORS_ORIGINS`           | the public URL of `nsp-frontend` after it deploys, e.g. `https://nsp-frontend.onrender.com` |
| `DATABASE_URL`           | the connection string for your externally-managed Postgres instance (`dpg-dbilglgae00c73c1fpag-a` / `smartposdb_g4mk`). Paste it exactly as Render shows it — the app rewrites `postgresql://` to `postgresql+psycopg2://` automatically. |

### nsp-frontend (required)

| Variable         | Value                                                      |
| ---------------- | ---------------------------------------------------------- |
| `VITE_API_BASE`  | the public URL of `nsp-backend`, e.g. `https://nsp-backend.onrender.com` |

## 3. Bootstrap the database

The first deploy runs `flask db upgrade` automatically (see
`preDeployCommand` in `render.yaml`). After the service is **live**, create
the superadmin account:

```
render run --service nsp-backend --command "flask create-superadmin --email you@example.com --password <strong-password>"
```

(Or connect to the service's shell from the Render dashboard and run the
same command.)

## 4. Verify

- Health check: `curl https://nsp-backend.onrender.com/api/health`
  → `{"status":"ok","database":"ok",...}`
- Frontend: open `https://nsp-frontend.onrender.com` in a browser and log in.

## 5. M-Pesa callback registration

Safaricom's STK push callback is an unauthenticated POST. The backend
gates it on `?token=<MPESA_CALLBACK_TOKEN>` when that env var is set.
Register this URL with Safaricom:

```
https://nsp-backend.onrender.com/api/mpesa/callback?token=<MPESA_CALLBACK_TOKEN>
```

## 6. Scheduled jobs

The backend ships a cron-style job for expense-due reminders
(`backend/scripts/expense_due_reminders.py`). Run it daily on Render with:

```
render run --service nsp-backend --command "python scripts/expense_due_reminders.py"
```

Or schedule it as a **Render Cron Job** (Cron Service) pointed at the same
command. The job is idempotent: an expense is reminded once per due date.

## 7. File storage

Business logos and item images are written to
`/opt/render/project/backend/uploads` inside the service container. The
folder is recreated on every deploy (see `app/__init__.py`), so existing
uploads survive deploys. If you need persistence across *instance
restarts*, attach a Render Disk to the backend service and set
`UPLOAD_FOLDER` to the disk mount path.

## 8. Scaling

The blueprint uses the **Starter** plan. To scale horizontally:

1. Change `plan` in `render.yaml` to a higher tier (e.g. `standard`).
2. Adjust the instance count in the dashboard.

Gunicorn is already configured for multiple workers
(`backend/gunicorn.conf.py`); a higher instance count gives you more
workers.

## 9. Alternative: Docker deployment

If you prefer to run the image elsewhere, build it with:

```
docker build -t nsp-backend -f backend/Dockerfile .
```

See `backend/Dockerfile` for the supported environment variables.