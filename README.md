# Terra · Acquisition Intelligence (United Brothers · SFR)

A reusable residential-acquisition analysis app with an institutional-grade UI.
The deterministic engine is a **100%-parity port** of the `Potential Targets` Excel
model (scoring + DCF); the app exposes it as REST tools + a best-in-class proptech
UI + **ATLAS**, a LUMEN-style copilot.

## UI (the "Terra" front end)
Left-rail proptech layout (Inter + Fraunces, ink/emerald palette):
- **Dashboard** — KPI tiles, tier doughnut, geographic-concentration bars, yield
  histogram, HHI concentration-risk monitor.
- **Target Map** — interactive Leaflet map (CARTO Positron), up to 9k clustered
  targets colored by score, filterable, click → property profile.
- **Targets** — sortable table → slide-over property **drawer** (facts, quick
  underwrite at 90% AVM, mini-map).
- **Underwriting** — two-way (Forward / Reverse goal-seek) with result cards.
- **Portfolio DCF** — Base/Downside/Upside scenarios, levered-CF + DSCR chart,
  scenario comparison, sources & uses.
- **ATLAS** — slide-in copilot (tool-use over the engine).

A self-contained static preview with embedded data is generated at
`../Terra_Preview.html` (double-click to open — no server needed).

## Run locally
```bash
pip install -r requirements.txt
python app.py                # -> http://127.0.0.1:5000
python evals/run_tests.py    # the gate (offline, must pass before deploy)
```
The app reads the canonical store in `../data/` (`scored.parquet`, `settings.json`,
`fix_params.json`) produced by the engine pipeline in the parent folder.

## Architecture (mirrors the Sales Rate Agent)
```
data/ (canonical store)  ->  re_engine.py / re_underwrite.py  (deterministic core)
                                   |                    |
                            REST  /api/*           assistant.py (ATLAS)
                                   |                    |
                                 templates/index.html  (UI + chat panel)
```
- **re_engine.py** — buy box, gate, 4-pillar score, risk haircut, tiering. Parity-tested.
- **re_underwrite.py** — single-property underwrite, **reverse goal-seek** solver, portfolio DCF.
- **data_guard.py** — fail-closed integrity gate for new data drops.
- **assistant.py** — ATLAS: tool-use loop over the engine (no math in the LLM).
- **evals/run_tests.py** — parity + reverse-solver + DCF gate.

## Tools (work without an API key)
`POST /api/market_summary | search_targets | lookup_property | underwrite | reverse_solve | portfolio_dcf`

## ATLAS chat
Set `ANTHROPIC_API_KEY` (model `claude-opus-4-8`, override via `ATLAS_MODEL`).
Without a key the app runs fully in **offline mode** — every tool still works.

## Deploy (Railway)
`Procfile` is included. Mount a volume at the data path; run `evals/run_tests.py`
in CI. See `OFFLINE_vs_AI.md` for the build philosophy and maturity path.

## Refresh with new data (one command)
```bash
python refresh.py                 # gate -> re-score -> rewrite scored.parquet + stamp
python refresh.py --universe new.parquet --workbook   # also rewrite the Excel table
```
Fail-closed gate, recomputes per-state blended costs from the new Tier-1 mix, and
stamps `data/refresh.json`. Structured table refs keep every workbook link intact.

## Auth (deploy)
Off by default (local/preview need no login). To enable:
```bash
AUTH_ENABLED=1 ADMIN_EMAILS=you@co.com ADMIN_PASSWORD=... SECRET_KEY=... python app.py
python auth.py analyst@co.com 'password' Analyst     # add a user (PBKDF2)
```
Capabilities: read · underwrite · chat · admin. Routes gate to `/login`; `/api/*` returns 401.

## ATLAS without a key
ATLAS has a deterministic **offline brain** (intent router over the same tools), so the
copilot answers with no `ANTHROPIC_API_KEY`. Set the key to upgrade to free-form LLM tool-use.
