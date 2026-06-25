# Deploying Terra · Acquisition Intelligence

Production target: **Railway** (same stack as the UB Sales Rate Agent), gunicorn WSGI,
with a **Volume** for writable data and **seed-on-boot** to restore the read-only
reference data on first start.

Validated locally: seed-on-boot restores all reference files into an empty data dir,
and the app serves under a production WSGI server (`/health`, `/`, report exports all 200).

---

## What's in this folder (the deploy artifact)
- `app.py` … the Flask app (gunicorn entrypoint `app:app`)
- `re_*.py`, `profiles.py`, `projects.py`, `assistant.py`, `auth.py`, `collab.py`,
  `feedback.py`, `reports.py`, `data_guard.py` … engine + platform modules
- `templates/index.html` … the Terra UI
- `seed/` … read-only reference data (scored.parquet + CSVs + settings) restored on boot
- `requirements.txt`, `Procfile`, `railway.json`, `.python-version`, `.env.example`
- `evals/run_tests.py` … the gate (run in CI before deploy)

`data/` (the runtime-writable store: projects, users.json, collab, feedback, uploads)
is **git-ignored** — it lives on the Railway Volume and is seeded from `seed/` on boot.

---

## 1. Push to GitHub
From inside this folder:
```bash
git init
git add -A
git commit -m "Terra Acquisition Intelligence — initial deploy"
gh repo create terra-acquisition --private --source=. --push
# or: git remote add origin git@github.com:<you>/terra-acquisition.git && git push -u origin main
```

## 2. Create the Railway service
1. **railway.app → New Project → Deploy from GitHub repo** → pick `terra-acquisition`.
   Railway auto-detects Python (nixpacks) and uses `railway.json`'s start command
   (`gunicorn app:app --workers 1 --threads 8 --bind 0.0.0.0:$PORT`).
2. **Add a Volume**: service → **Volumes → New Volume**, mount path **`/app/data`**.
   *(Critical — without it, users/projects/feedback wipe on every redeploy.)*
3. **Set Variables** (service → Variables):
   | Variable | Value |
   |---|---|
   | `RE_DATA` | `/app/data` |
   | `SECRET_KEY` | a long random string |
   | `AUTH_ENABLED` | `1` (turns on sign-in + firewall) |
   | `ADMIN_EMAILS` | `AshokR@unitedbrothersnv.com` |
   | `ADMIN_PASSWORD` | a temporary admin password |
   | `ANTHROPIC_API_KEY` | *(optional — enables full ATLAS; offline mode works without)* |
4. **Deploy.** First boot seeds `seed/ → /app/data`, builds the default SFR project,
   and the healthcheck hits `/health`.

## 3. First sign-in
- Open the Railway URL → **Create account** with `ADMIN_EMAILS` (auto-approved as Admin),
  or sign in with `ADMIN_EMAILS` + `ADMIN_PASSWORD`.
- New teammates self-register → land as **pending** → approve them at **/admin/users**.

---

## Operational notes
- **`--workers 1` is load-bearing.** The app holds the scored universe, the active
  project, and the rate-limiter in memory; multiple workers would each get a separate
  copy. Use `--threads` for concurrency, not workers.
- **Redeploys keep volume data.** Code redeploys don't touch `/app/data`; seed-on-boot
  only fills files that are missing.
- **Updating the universe:** re-run `python refresh.py --workbook` locally → copy the new
  `data/scored.parquet` (+ changed CSVs) into `seed/`, commit, push. Or upload a dataset
  in-app (Setup → upload) to create a new project without redeploying.
- **Turn the firewall on for production:** set `AUTH_ENABLED=1` and a strong `SECRET_KEY`.
  Headers, rate-limit (300/60s/IP) and cross-origin POST blocking activate automatically.

## CI gate (recommended)
Add a GitHub Action that runs the gate on every push:
```yaml
# .github/workflows/ci.yml
name: gate
on: [push]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - run: pip install -r requirements.txt
      - run: RE_DATA=$PWD/seed python evals/run_tests.py
```

## Alternatives
- **Render / Fly.io:** same `gunicorn app:app` start command + a persistent disk at
  `RE_DATA`; set the same env vars.
- **Docker:** `python:3.12-slim`, `pip install -r requirements.txt`,
  `CMD gunicorn app:app --workers 1 --threads 8 --bind 0.0.0.0:$PORT`, volume at `/app/data`.
- **Local production test (Windows):** `pip install waitress` then
  `python -m waitress --port=8080 app:app`.
