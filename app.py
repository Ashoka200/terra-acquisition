"""
app.py — RE Acquisition Agent (Flask). Mirrors the Sales Rate Agent shape:
  * deterministic engine tools exposed as REST endpoints (work offline, no key)
  * ATLAS chat copilot on top (needs ANTHROPIC_API_KEY)
Run:  python app.py    ->  http://127.0.0.1:5000
"""
import os, json
import pandas as pd
from flask import Flask, request, jsonify, render_template, render_template_string, redirect, session

import re_engine as E
import re_underwrite as U
import assistant
import auth
import projects, profiles

DATA = os.environ.get("RE_DATA", os.path.join(os.path.dirname(__file__), "..", "data"))
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-prod")

# numpy-safe + NaN-safe JSON.
#  - default(): pandas/iloc returns np.int64/float64 which Flask can't serialize.
#  - dumps():   NaN/Inf are INVALID JSON. A Python/numpy float('nan') is emitted as a
#    literal `NaN` BEFORE default() ever runs (float subclass), which the browser's
#    JSON.parse rejects — that left Tier 2/3 Targets stuck on "searching…" because some
#    rows have NaN `tenure`. So we pre-walk the payload and convert NaN/Inf → null.
import math as _math
import numpy as np
from flask.json.provider import DefaultJSONProvider
def _json_clean(o):
    if isinstance(o, float):  # also catches np.floating (a float subclass)
        return None if (_math.isnan(o) or _math.isinf(o)) else o
    if isinstance(o, dict):
        return {k: _json_clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_json_clean(v) for v in o]
    return o
class _NPJSON(DefaultJSONProvider):
    def default(self, o):
        if isinstance(o, np.integer): return int(o)
        if isinstance(o, np.floating): return None if np.isnan(o) else float(o)
        if isinstance(o, np.ndarray): return o.tolist()
        return super().default(o)
    def dumps(self, obj, **kwargs):
        return super().dumps(_json_clean(obj), **kwargs)
app.json = _NPJSON(app)

# ---------------- security firewall: rate limit + headers + CSRF ----------------
import time as _time
from collections import deque, defaultdict
_HITS = defaultdict(deque)
RATE_N, RATE_W = 300, 60   # 300 requests / 60s per IP
@app.before_request
def _ratelimit():
    if request.path.startswith("/static"): return
    ip = (request.headers.get("X-Forwarded-For", request.remote_addr or "?")).split(",")[0].strip()
    q = _HITS[ip]; now = _time.time()
    while q and q[0] < now - RATE_W: q.popleft()
    if len(q) >= RATE_N: return jsonify(error="rate limit exceeded — slow down"), 429
    q.append(now)
@app.after_request
def _sec_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "SAMEORIGIN"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["X-XSS-Protection"] = "1; mode=block"
    if request.path.startswith(("/api", "/chat")):
        resp.headers["Cache-Control"] = "no-store"
    return resp

# ---------------- seed-on-boot (restores read-only data into an empty volume) ----------------
import shutil
def _seed_data():
    seed = os.path.join(os.path.dirname(__file__), "seed")
    if not os.path.isdir(seed): return
    os.makedirs(DATA, exist_ok=True)
    for fn in os.listdir(seed):
        src = os.path.join(seed, fn); dst = os.path.join(DATA, fn)
        if os.path.isfile(src) and not os.path.exists(dst):   # files only — projects/ is created fresh
            shutil.copy(src, dst)
_seed_data()

# ---------------- load canonical store on startup ----------------
print("loading scored universe ...")
SCORED = pd.read_parquet(os.path.join(DATA, "scored.parquet"))
SETTINGS = json.load(open(os.path.join(DATA, "settings.json")))
FX = json.load(open(os.path.join(DATA, "fix_params.json")))
REFS = E.load_refs(DATA)
ACTIVE_PID = projects.ensure_default(REFS, FX)
print("  loaded", len(SCORED), "rows · active project:", ACTIVE_PID)

# default single-property assumptions (from the model)
ASSUMP = dict(closing=0.03, rehab=15000, vacancy=0.05, pm=0.08, maint=0.05,
              tax=FX["blended_tax_pct"], ins=int(FX["blended_insurance"]), hoa=0,
              other=300, ltv=0.70, rate=0.0725, amort=30, points=0.01)
# per-APN underwriting overrides (persisted on the active project)
UW_OVERRIDES = (projects.get_project(ACTIVE_PID) or {}).get("uw_overrides", {})
def _eff_assump(apn):
    """Effective assumptions for a property: portfolio defaults + any saved per-APN override."""
    return _merge_assump(ASSUMP, UW_OVERRIDES.get(str(apn), {}))

PORT_COMMON = dict(homes=100, acq=0.02, rehab=5000, rent_growth=0.03, exp_growth=0.025,
                   vacancy=0.05, pm=0.08, rm=0.05, other_home=300, hoa_home=0,
                   ltv=0.70, rate=0.0725, amort=30, io_years=5, hold=7, exit_cap=0.065,
                   selling=0.02, loan_fee=0.01,
                   price_home=FX["tier1_avg_avm"]*FX["fixes"]["avm_discount"],
                   rent_home=FX["tier1_avg_marketrent"], tax=FX["blended_tax_pct"],
                   ins_home=int(FX["blended_insurance"]), capex_home=FX["capex_per_home"])

# ---------------- engine tools (also the chatbot's tools) ----------------
import re as _re
STATE_CODES = {"AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA","KS","KY",
    "LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ","NM","NY","NC","ND","OH","OK",
    "OR","PA","RI","SC","SD","TN","TX","UT","VT","VA","WA","WV","WI","WY","DC"}
STATE_NAMES = {"alabama":"AL","alaska":"AK","arizona":"AZ","arkansas":"AR","california":"CA","colorado":"CO",
    "connecticut":"CT","delaware":"DE","florida":"FL","georgia":"GA","hawaii":"HI","idaho":"ID","illinois":"IL",
    "indiana":"IN","iowa":"IA","kansas":"KS","kentucky":"KY","louisiana":"LA","maine":"ME","maryland":"MD",
    "massachusetts":"MA","michigan":"MI","minnesota":"MN","mississippi":"MS","missouri":"MO","montana":"MT",
    "nebraska":"NE","nevada":"NV","new hampshire":"NH","new jersey":"NJ","new mexico":"NM","new york":"NY",
    "north carolina":"NC","north dakota":"ND","ohio":"OH","oklahoma":"OK","oregon":"OR","pennsylvania":"PA",
    "rhode island":"RI","south carolina":"SC","south dakota":"SD","tennessee":"TN","texas":"TX","utah":"UT",
    "vermont":"VT","virginia":"VA","washington":"WA","west virginia":"WV","wisconsin":"WI","wyoming":"WY"}

# major metro / city names → state (so "Atlanta" resolves to GA even when the city
# field holds suburb names). Covers the Sun-Belt book; extend as needed.
METRO_STATE = {"atlanta":"GA","dallas":"TX","fort worth":"TX","houston":"TX","san antonio":"TX",
    "austin":"TX","el paso":"TX","charlotte":"NC","raleigh":"NC","greensboro":"NC","memphis":"TN",
    "nashville":"TN","knoxville":"TN","chattanooga":"TN","phoenix":"AZ","tucson":"AZ","mesa":"AZ",
    "tampa":"FL","orlando":"FL","jacksonville":"FL","miami":"FL","fort lauderdale":"FL","las vegas":"NV",
    "reno":"NV","birmingham":"AL","montgomery":"AL","huntsville":"AL","columbia":"SC","charleston":"SC",
    "savannah":"GA","augusta":"GA","macon":"GA"}

def _loc_match(q, loc):
    """Filter by a free-text location: 2-letter state code, full state name, metro/city name."""
    if not loc: return q
    s = str(loc).strip()
    if not s: return q
    su, sl = s.upper(), s.lower()
    states = q["state"].astype(str).str.upper()
    if len(su) == 2 and su in STATE_CODES:
        return q[states == su]
    if sl in STATE_NAMES:
        return q[states == STATE_NAMES[sl]]
    # city contains the term?
    cm = q["city"].astype(str).str.upper().str.contains(_re.escape(su), na=False, regex=True)
    hit = q[cm]
    # metro alias → also pull the whole state (union with any literal city hits)
    if sl in METRO_STATE:
        return q[(states == METRO_STATE[sl]) | cm]
    if len(hit):
        return hit
    return q[states == su]  # last resort: maybe a loosely-typed code

def t_market_summary():
    vc = SCORED["tier"].value_counts()
    t1 = SCORED[SCORED["tier"] == "Tier 1 - Strong"]
    geo = t1["state"].value_counts()
    shares = (geo / len(t1))
    hhi = int((shares ** 2).sum() * 10000)
    return {"tiers": vc.to_dict(), "match_rate": round((SCORED["tier"] != "Not a Match").mean(), 4),
            "tier1_top_states": geo.head(6).to_dict(), "hhi": hhi,
            "tier1_avg_yield": round(float(t1["gross_yield"].mean()), 4),
            "tier1_avg_avm": round(float(t1["avm"].mean()), 0)}

def t_search_targets(tier="Tier 1 - Strong", state=None, min_yield=None,
                     max_price=None, corp_only=False, limit=25):
    q = SCORED[SCORED["tier"] == tier] if tier else SCORED
    if state: q = _loc_match(q, state)
    if min_yield is not None: q = q[q["gross_yield"] >= min_yield]
    if max_price is not None: q = q[q["avm"] <= max_price]
    if corp_only: q = q[q["corp"] == "Y"]
    q = q.sort_values("total_score", ascending=False).head(int(limit))
    return {"count": len(q), "rows": q[["apn","address","city","state","zip","lat","lon",
            "yearbuilt","sqft","beds","avm","market_rent","gross_yield","tenure","corp",
            "total_score","tier"]].round(5).to_dict("records")}

def t_lookup_property(query):
    q = str(query).strip().upper()
    hit = SCORED[SCORED["apn"].astype(str).str.upper() == q]
    if hit.empty:
        hit = SCORED[SCORED["address"].astype(str).str.upper().str.contains(q, na=False)]
    if hit.empty:
        return {"found": False}
    r = hit.iloc[0]
    return {"found": True, **{k: (round(float(r[k]),5) if isinstance(r[k],(int,float)) else r[k])
            for k in ["apn","address","city","state","zip","lat","lon","yearbuilt","sqft","avm",
                      "beds","corp","market_rent","gross_yield","tenure","total_score","tier"]}}

import calc_trace
@app.route("/api/calc", methods=["POST"])
def api_calc():
    b = request.get_json(force=True)
    r = t_lookup_property(b.get("query") or b.get("apn", ""))
    if not r.get("found"):
        return jsonify(error="property not found"), 404
    prof = projects.get_project(ACTIVE_PID)["profile"]
    return jsonify(calc_trace.trace(r, REFS, prof, ASSUMP))

import risk_model
@app.route("/api/risk", methods=["POST"])
def api_risk():
    b = request.get_json(force=True)
    r = t_lookup_property(b.get("query") or b.get("apn", ""))
    if not r.get("found"):
        return jsonify(error="property not found"), 404
    a = ASSUMP
    out = risk_model.assess(r, REFS, rate=float(a.get("rate", 0.07)), ltv=float(a.get("ltv", 0.75)),
                            opex_ratio=float(a.get("opex_ratio", a.get("opex", 0.42))))
    out["address"] = r.get("address"); out["apn"] = r.get("apn")
    return jsonify(out)

_AKEYS = ("closing", "rehab", "vacancy", "pm", "maint", "tax", "ins", "hoa", "other", "ltv", "rate", "amort", "points")
def _merge_assump(base, over):
    a = dict(base)
    for k in _AKEYS:
        if over.get(k) is not None:
            try: a[k] = float(over[k])
            except (TypeError, ValueError): pass
    return a

def t_underwrite(price, monthly_rent, rate=None, ltv=None, **over):
    a = _merge_assump(ASSUMP, {**over, "rate": rate, "ltv": ltv})
    return U.underwrite(price, monthly_rent, a)

def t_reverse_solve(target_metric, target, monthly_rent, rate=None, ltv=None, **over):
    a = _merge_assump(ASSUMP, {**over, "rate": rate, "ltv": ltv})
    P = U.reverse_price(target_metric, target, a, monthly_rent)
    return {"implied_price": round(P, 0), "verify": U.underwrite(P, monthly_rent, a), "assumptions": a}

_OVKEYS = _AKEYS + ("price", "rent")
@app.route("/api/uw/property", methods=["POST"])
def api_uw_property():
    """Load a property + its effective underwriting inputs (saved override else defaults)."""
    b = request.get_json(force=True)
    r = t_lookup_property(b.get("query") or b.get("apn", ""))
    if not r.get("found"): return jsonify(found=False)
    apn = str(r["apn"]); ov = UW_OVERRIDES.get(apn, {})
    price = ov.get("price", round(float(r["avm"]) * 0.9)) if r.get("avm") else ov.get("price", 0)
    rent = ov.get("rent", r.get("market_rent") or 0)
    return jsonify(found=True, property=r, price=price, rent=rent,
                   assumptions=_eff_assump(apn), has_override=apn in UW_OVERRIDES, defaults=dict(ASSUMP))

@app.route("/api/uw/save", methods=["POST"])
def api_uw_save():
    b = request.get_json(force=True); ov = b.get("assumptions", {}) or {}
    clean = {}
    for k in _OVKEYS:
        if ov.get(k) is not None:
            try: clean[k] = float(ov[k])
            except (TypeError, ValueError): pass
    if b.get("scope") == "portfolio":
        for k in _AKEYS:
            if k in clean: ASSUMP[k] = clean[k]
        d = projects.get_project(ACTIVE_PID); d["assumptions_uw"] = dict(ASSUMP); projects.save_project(d)
        return jsonify(ok=True, scope="portfolio", assumptions=dict(ASSUMP))
    apn = str(b.get("apn", ""))
    if not apn: return jsonify(error="no apn"), 400
    UW_OVERRIDES[apn] = clean
    d = projects.get_project(ACTIVE_PID); d["uw_overrides"] = UW_OVERRIDES; projects.save_project(d)
    return jsonify(ok=True, scope="property", apn=apn)

@app.route("/api/uw/clear", methods=["POST"])
def api_uw_clear():
    apn = str(request.get_json(force=True).get("apn", ""))
    UW_OVERRIDES.pop(apn, None)
    d = projects.get_project(ACTIVE_PID); d["uw_overrides"] = UW_OVERRIDES; projects.save_project(d)
    return jsonify(ok=True, cleared=apn)

SCENARIOS = {  # Base / Downside / Upside drivers (from the SFR model)
    "Base":     dict(rent_growth=0.03,  exp_growth=0.025, vacancy=0.05, rate=0.0725, exit_cap=0.065),
    "Downside": dict(rent_growth=0.015, exp_growth=0.035, vacancy=0.08, rate=0.0825, exit_cap=0.0725),
    "Upside":   dict(rent_growth=0.04,  exp_growth=0.02,  vacancy=0.04, rate=0.065,  exit_cap=0.06),
}
def t_portfolio_dcf(scenario="Base", homes=None, rate=None, exit_cap=None, rent_growth=None):
    p = dict(PORT_COMMON)
    p.update(SCENARIOS.get(scenario, SCENARIOS["Base"]))
    for k, v in {"homes": homes, "rate": rate, "exit_cap": exit_cap, "rent_growth": rent_growth}.items():
        if v is not None: p[k] = v
    out = U.portfolio_dcf(p); out["scenario"] = scenario
    # one-shot all 3 scenarios for the comparison card
    out["compare"] = {s: {kk: U.portfolio_dcf({**PORT_COMMON, **dv})[kk]
                          for kk in ("levered_irr","equity_multiple","min_dscr")}
                      for s, dv in SCENARIOS.items()}
    return out

def t_map_points(tier="Tier 1 - Strong", state=None, min_yield=None, max_price=None, limit=8000):
    q = SCORED[(SCORED["tier"] == tier) & SCORED["lat"].notna()] if tier else SCORED[SCORED["lat"].notna()]
    if state: q = _loc_match(q, state)
    if min_yield is not None: q = q[q["gross_yield"] >= min_yield]
    if max_price is not None: q = q[q["avm"] <= max_price]
    q = q.sort_values("total_score", ascending=False).head(int(limit))
    pts = [[round(float(r.lat),5), round(float(r.lon),5), round(float(r.total_score),1),
            round(float(r.gross_yield),4), int(r.avm), str(r.apn), str(r.address), str(r.corp)]
           for r in q.itertuples()]
    return {"count": len(pts), "fields": ["lat","lon","score","yield","avm","apn","address","corp"], "points": pts}

def _hist(s, bins):
    import numpy as np
    s = pd.to_numeric(s, errors="coerce").dropna()
    c, e = np.histogram(s, bins=bins)
    return {"counts": [int(x) for x in c], "edges": [round(float(x),4) for x in e]}

def t_analytics():
    t1 = SCORED[SCORED["tier"] == "Tier 1 - Strong"]
    geo = t1["state"].value_counts()
    shares = geo / len(t1)
    hhi = int((shares ** 2).sum() * 10000)
    over = {s: round(float(v),3) for s, v in shares.items() if v > 0.35}
    return {
        "tiers": SCORED["tier"].value_counts().to_dict(),
        "state_concentration": geo.head(8).to_dict(),
        "state_shares": {s: round(float(v),4) for s, v in shares.head(8).items()},
        "hhi": hhi, "over_cap": over,
        "yield_hist": _hist(t1["gross_yield"], [0,.05,.06,.07,.08,.09,.10,.11,.12,.15]),
        "score_hist": _hist(t1["total_score"], 10),
        "avm_hist": _hist(t1["avm"], [150000,200000,250000,300000,350000,400000]),
        "corp_split": t1["corp"].value_counts().to_dict(),
        "tier1_avg_yield": round(float(t1["gross_yield"].mean()),4),
        "tier1_avg_avm": round(float(t1["avm"].mean()),0),
        "tier1_avg_tenure": round(float(t1["tenure"].mean()),1),
        "tier1_corp_share": round(float((t1["corp"]=="Y").mean()),3),
    }

import site_intel
def t_geocode(query):
    import requests
    try:
        r = requests.get("https://nominatim.openstreetmap.org/search",
            params={"format": "json", "addressdetails": 1, "limit": 1, "q": query},
            headers={"User-Agent": "Terra/2.0"}, timeout=15).json()
        if not r: return {"found": False}
        return {"found": True, "lat": float(r[0]["lat"]), "lon": float(r[0]["lon"]),
                "zip": ((r[0].get("address", {}) or {}).get("postcode") or "").split(";")[0],
                "display": r[0].get("display_name")}
    except Exception as e:
        return {"found": False, "error": str(e)[:90]}

def t_site_analysis(lat, lon):
    return site_intel.analyze(float(lat), float(lon))

def t_massing_tool(area, use="hotel", shape="rectangular", far=2.5, height=65, coverage=0.45, parking=1.0):
    return site_intel.massing(float(area), use=use, shape=shape, far=float(far),
                              height_ft=float(height), lot_coverage=float(coverage), parking_ratio=float(parking))

def t_explain_calc(query):
    """Trace one deal's gate/score/underwriting and return a compact driver summary."""
    r = t_lookup_property(query=query)
    if not r.get("found"): return {"found": False}
    prof = projects.get_project(ACTIVE_PID)["profile"]
    tr = calc_trace.trace(r, REFS, prof, ASSUMP)
    s = tr["score"]
    drivers = sorted(s["rows"], key=lambda x: -x["contribution"])[:4]
    uw = {x["label"]: x["value"] for x in tr["underwrite"]["rows"]}
    return {"found": True, "address": r.get("address"), "apn": r.get("apn"),
            "gate_passed": tr["gate"]["passed"], "gate_verdict": tr["gate"]["verdict"],
            "tier": s["tier"], "total_score": s["total"], "raw_score": s["raw_score"],
            "risk_haircut": s["haircut"], "ties_stored": tr["verify"]["ties"],
            "top_drivers": [{"metric": d["metric"], "pillar": d["pillar"], "sub": d["subscore"],
                             "weight": d["weight"], "contribution": d["contribution"]} for d in drivers],
            "cap_rate": uw.get("Going-in cap rate"), "coc": uw.get("Cash-on-cash"),
            "dscr": uw.get("DSCR"), "offer_price": tr["underwrite"]["price"]}

def t_risk(query):
    """Multi-dimension acquisition risk for one property."""
    r = t_lookup_property(query=query)
    if not r.get("found"): return {"found": False}
    out = risk_model.assess(r, REFS, rate=float(ASSUMP.get("rate", 0.07)), ltv=float(ASSUMP.get("ltv", 0.7)))
    return {"found": True, "address": r.get("address"), "grade": out["grade"], "score": out["score"],
            "band": out["band"], "decision": out["decision"], "summary": out["summary"],
            "top_flags": [{"severity": f["severity"], "category": f["category"], "title": f["title"],
                           "mitigation": f["mitigation"]} for f in out["top"]]}

DISPATCH = {"market_summary": t_market_summary, "search_targets": t_search_targets,
            "lookup_property": t_lookup_property, "underwrite": t_underwrite,
            "reverse_solve": t_reverse_solve, "portfolio_dcf": t_portfolio_dcf,
            "map_points": t_map_points, "analytics": t_analytics,
            "geocode": t_geocode, "site_analysis": t_site_analysis, "massing": t_massing_tool,
            "explain_calc": t_explain_calc, "risk": t_risk}

# ---------------- projects / model studio ----------------
@app.route("/api/types")
def api_types():
    out = profiles.list_types()
    for t in out: t["canonical"] = projects.CANONICAL.get(t["type"], [])
    return jsonify(out)

@app.route("/api/projects")
def api_projects(): return jsonify({"active": ACTIVE_PID, "projects": projects.list_projects()})

@app.route("/api/active")
def api_active():
    d = projects.get_project(ACTIVE_PID)
    return jsonify({"id": d["id"], "name": d["name"], "type": d["type"],
                    "profile": d["profile"], "assumptions": d.get("assumptions", {}),
                    "canonical": projects.CANONICAL.get(d["type"], []), "rows": d.get("rows", 0)})

@app.route("/api/project/create", methods=["POST"])
def api_proj_create():
    b = request.get_json(force=True)
    d = projects.create_project(b["name"], b["type"], REFS, FX,
                                source_path=b.get("source"), column_map=b.get("column_map"))
    scored = None
    if b.get("source"):
        try: scored = len(projects.load_scored(d["id"], REFS))
        except Exception as e: scored = "error: " + str(e)[:80]
    return jsonify({"id": d["id"], "name": d["name"], "type": d["type"], "scored_rows": scored})

@app.route("/upload", methods=["POST"])
def upload():
    f = request.files.get("file")
    if not f: return jsonify(error="no file"), 400
    import werkzeug.utils
    name = werkzeug.utils.secure_filename(f.filename)
    if not name.lower().endswith((".csv", ".parquet", ".xlsx")): return jsonify(error="csv/xlsx/parquet only"), 400
    updir = os.path.join(DATA, "uploads"); os.makedirs(updir, exist_ok=True)
    path = os.path.join(updir, name); f.save(path)
    try:
        if name.endswith(".parquet"): df = pd.read_parquet(path)
        elif name.endswith(".xlsx"): df = pd.read_excel(path, nrows=200)
        else: df = pd.read_csv(path, nrows=200)
    except Exception as e:
        return jsonify(error="could not read file: " + str(e)[:100]), 400
    return jsonify({"path": path, "columns": [str(c) for c in df.columns], "rows": int(len(df))})

@app.route("/api/project/activate", methods=["POST"])
def api_proj_activate():
    global ACTIVE_PID, SCORED
    pid = request.get_json(force=True)["id"]
    if not projects.get_project(pid): return jsonify(error="no such project"), 404
    ACTIVE_PID = pid
    try:
        SCORED = projects.load_scored(pid, REFS); has_data = True
    except Exception:
        has_data = False
    return jsonify({"active": ACTIVE_PID, "rows": len(SCORED) if has_data else 0, "has_data": has_data})

@app.route("/api/project/profile", methods=["POST"])
def api_proj_profile():
    b = request.get_json(force=True)
    d = projects.update_profile(b.get("id", ACTIVE_PID), b["patch"])
    return jsonify({"ok": bool(d), "profile": d["profile"] if d else None})

import re_core
DISPLAY_COLS = ["apn", "address", "city", "state", "zip", "lat", "lon", "yearbuilt", "sqft",
                "avm", "beds", "corp", "market_rent", "gross_yield", "tenure", "total_score", "tier"]
@app.route("/api/rescore", methods=["POST"])
def api_rescore():
    """Re-run the engine with the active project's CURRENT profile so edits (weights, gate,
    added/removed metrics, tiers, risk) actually change the Targets/Dashboard/Map."""
    global SCORED
    d = projects.get_project(ACTIVE_PID)
    if not d: return jsonify(error="no active project"), 400
    prof = d["profile"]; fixes = prof.get("fixes", {"avm_discount": 0.9, "rent_realization": 0.95})
    if isinstance(fixes, dict) and "fixes" in fixes: fixes = fixes["fixes"]
    src = d.get("source")
    try:
        if src:
            raw = pd.read_parquet(src) if src.endswith(".parquet") else pd.read_csv(src)
            raw = projects.apply_map(raw, d.get("column_map"))
        else:
            raw = pd.read_parquet(os.path.join(DATA, "universe_raw.parquet"))
        scored, _ = re_core.run(raw, REFS, fixes, ptype=d["type"], profile=prof)
    except Exception as e:
        return jsonify(error="re-score failed: " + str(e)[:160]), 500
    if "market_rent" not in scored.columns and "max_rent" in scored.columns:
        scored["market_rent"] = (pd.to_numeric(scored["max_rent"], errors="coerce") * fixes.get("rent_realization", 1)).round(0)
    SCORED = scored[[c for c in DISPLAY_COLS if c in scored.columns]].copy()
    try: SCORED.to_parquet(os.path.join(DATA, "scored.parquet"), index=False)
    except Exception: pass
    return jsonify(rows=len(SCORED), tiers=SCORED["tier"].value_counts().to_dict(),
                   scored_with=[m["key"] for m in prof["metrics"] if m.get("on", True) and m.get("input") in scored.columns])

# ---------------- DATA ROOM: per-project downloads + upload-and-compare ----------------
import compare
CANDIDATES = {}  # pid -> {"raw_path", "scored", "compare"} staged upload awaiting Apply

def _project_raw(d):
    """The effective raw input for a project (its own source, else the master universe)."""
    src = d.get("source")
    if src and os.path.exists(src):
        raw = pd.read_parquet(src) if src.endswith(".parquet") else pd.read_csv(src)
        return projects.apply_map(raw, d.get("column_map"))
    return pd.read_parquet(os.path.join(DATA, "universe_raw.parquet"))

def _auto_map(columns, ptype):
    cols = [str(c) for c in columns]; low = {c.lower(): c for c in cols}; out = {}
    for cf in projects.CANONICAL.get(ptype, []):
        k = cf.replace("?", "").lower()
        hit = low.get(k) or next((c for c in cols if k == c.lower().strip()), None) \
              or next((c for c in cols if k in c.lower()), None)
        if hit: out[cf.replace("?", "")] = hit
    return out

def _rep_property():
    """Highest-scoring Tier-1 row as the worked example for the project model workbook."""
    t1 = SCORED[SCORED["tier"] == "Tier 1 - Strong"]
    src = t1 if len(t1) else SCORED
    r = src.sort_values("total_score", ascending=False).iloc[0]
    return t_lookup_property(query=str(r["apn"]))

SOURCE_WB = os.path.join(DATA, "..", "Potential Targets - v2 (Fixed).xlsx")

@app.route("/api/project/dataroom")
def api_dataroom():
    d = projects.get_project(ACTIVE_PID) or {}
    rep = _rep_property() if len(SCORED) else {}
    arts = [
        {"key": "model", "name": "Firm model — full workbook (live formulas)", "fmt": "xlsx",
         "desc": "Replica of the firm's Excel model (reference tables excluded): Exec Summary, Dashboard, Buy Box, Methodology, Scoring Model, and a live 10-year SFR DCF. Buy box & assumptions are pulled from this project's Model Studio; change any input and it recomputes.",
         "url": "/api/report/project_model.xlsx", "available": bool(rep.get("found"))},
        {"key": "targets", "name": "Scored targets", "fmt": "xlsx",
         "desc": "Ranked Tier-1 targets with AVM, rent, yield, tenure and score.",
         "url": "/api/report/targets.xlsx?limit=500", "available": True},
        {"key": "dataset", "name": "Full scored dataset", "fmt": "csv",
         "desc": f"Every scored property in this project ({len(SCORED):,} rows) with tier and score.",
         "url": "/api/report/scored.csv", "available": True},
        {"key": "portfolio_pdf", "name": "Portfolio report", "fmt": "pdf",
         "desc": "10-year levered DCF — IRR, equity multiple, DSCR, cash flows.",
         "url": "/api/report/portfolio.pdf", "available": True},
        {"key": "portfolio_xlsx", "name": "Portfolio model", "fmt": "xlsx",
         "desc": "Portfolio cash-flow workbook with chart.", "url": "/api/report/portfolio.xlsx", "available": True},
        {"key": "source", "name": "Firm source workbook (reference)", "fmt": "xlsx",
         "desc": "The original de-bloated firm workbook this engine was ported from (100% parity). Large file; reference only.",
         "url": "/api/report/source.xlsx", "available": os.path.exists(SOURCE_WB)},
    ]
    return jsonify({"project": {"id": d.get("id"), "name": d.get("name"), "type": d.get("type"),
                    "rows": int(len(SCORED)), "source": "uploaded data" if d.get("source") else "master universe"},
                    "artifacts": [a for a in arts if a["available"] or a["key"] == "source"],
                    "snapshot": compare.snapshot(SCORED),
                    "staged": ACTIVE_PID in CANDIDATES})

@app.route("/api/report/scored.csv")
def rep_scored_csv():
    return _dl(SCORED.to_csv(index=False).encode(), "text/csv", "Terra_Scored_Dataset.csv")

@app.route("/api/report/project_model.xlsx")
def rep_project_model():
    rep = _rep_property()
    if not rep.get("found"): return jsonify(error="no scored rows"), 404
    prof = projects.get_project(ACTIVE_PID)["profile"]
    loc = calc_trace._loc_values(rep.get("zip"), REFS)
    tr = calc_trace.trace(rep, REFS, prof, ASSUMP)
    snap = compare.snapshot(SCORED)
    return _dl(xlmodel.firm_model(prof, ASSUMP, snap, dict(PORT_COMMON), rep, loc, tr, SCENARIOS),
               XLSX, "Terra_Firm_Model.xlsx")

@app.route("/api/report/source.xlsx")
def rep_source_wb():
    if not os.path.exists(SOURCE_WB):
        return jsonify(error="source workbook not present in this deployment"), 404
    from flask import send_file
    return send_file(SOURCE_WB, as_attachment=True, download_name="Potential_Targets_Firm_Model.xlsx")

@app.route("/api/project/upload_compare", methods=["POST"])
def api_upload_compare():
    """Score an uploaded dataset with the project's current model and diff vs the live universe."""
    f = request.files.get("file")
    if not f: return jsonify(error="no file"), 400
    import werkzeug.utils
    name = werkzeug.utils.secure_filename(f.filename) or "upload"
    if not name.lower().endswith((".csv", ".parquet", ".xlsx")):
        return jsonify(error="csv / xlsx / parquet only"), 400
    updir = os.path.join(DATA, "uploads"); os.makedirs(updir, exist_ok=True)
    path = os.path.join(updir, name); f.save(path)
    d = projects.get_project(ACTIVE_PID); prof = d["profile"]
    fixes = prof.get("fixes", {"avm_discount": 0.9, "rent_realization": 0.95})
    if isinstance(fixes, dict) and "fixes" in fixes: fixes = fixes["fixes"]
    try:
        up = pd.read_parquet(path) if name.endswith(".parquet") else \
             (pd.read_excel(path) if name.endswith(".xlsx") else pd.read_csv(path))
    except Exception as e:
        return jsonify(error="could not read file: " + str(e)[:120]), 400
    cmap = _auto_map(up.columns, d["type"])
    up = projects.apply_map(up, cmap)
    if "apn" not in up.columns and "id" in up.columns: up = up.rename(columns={"id": "apn"})
    if "proptype" not in up.columns and d["type"] == "SFR": up["proptype"] = "SFR"
    missing = [c for c in ("apn", "avm", "sqft", "yearbuilt", "zip") if c not in up.columns]
    if missing:
        return jsonify(error="couldn't map required columns: " + ", ".join(missing) +
                       ". Use the New-project wizard for a custom schema."), 400
    base = _project_raw(d)
    combined = pd.concat([base, up], ignore_index=True)
    for c in ("apn", "zip"):  # normalize mixed-type key cols so they persist cleanly
        if c in combined.columns: combined[c] = combined[c].astype(str)
    combined = combined.drop_duplicates("apn", keep="last")
    try:
        scored, _ = re_core.run(combined, REFS, fixes, ptype=d["type"], profile=prof)
    except Exception as e:
        return jsonify(error="re-score failed: " + str(e)[:140]), 500
    if "market_rent" not in scored.columns and "max_rent" in scored.columns:
        scored["market_rent"] = (pd.to_numeric(scored["max_rent"], errors="coerce") * fixes.get("rent_realization", 1)).round(0)
    new_scored = scored[[c for c in DISPLAY_COLS if c in scored.columns]].copy()
    raw_path = os.path.join(updir, "_candidate_%s.parquet" % ACTIVE_PID)
    try:
        combined.to_parquet(raw_path, index=False)
    except Exception:  # mixed object dtypes — fall back to CSV (read path handles both)
        raw_path = os.path.join(updir, "_candidate_%s.csv" % ACTIVE_PID)
        combined.to_csv(raw_path, index=False)
    diff = compare.compare(SCORED, new_scored)
    diff["upload"] = {"file": name, "rows_in_file": int(len(up)), "mapped": cmap}
    CANDIDATES[ACTIVE_PID] = {"raw_path": raw_path, "scored": new_scored, "compare": diff}
    return jsonify(diff)

@app.route("/api/project/apply_upload", methods=["POST"])
def api_apply_upload():
    global SCORED
    cand = CANDIDATES.get(ACTIVE_PID)
    if not cand: return jsonify(error="nothing staged"), 400
    d = projects.get_project(ACTIVE_PID)
    d["source"] = cand["raw_path"]; d["column_map"] = {}; d["rows"] = int(len(cand["scored"]))
    projects.save_project(d)
    SCORED = cand["scored"]
    try: SCORED.to_parquet(os.path.join(DATA, "scored.parquet"), index=False)
    except Exception: pass
    # invalidate the project's scored cache so reloads use the new data
    try: os.remove(os.path.join(projects.PROJ_DIR, ACTIVE_PID, "scored.parquet"))
    except Exception: pass
    cand["scored"].to_parquet(os.path.join(projects.PROJ_DIR, ACTIVE_PID, "scored.parquet"), index=False)
    CANDIDATES.pop(ACTIVE_PID, None)
    return jsonify(ok=True, rows=len(SCORED), tiers=SCORED["tier"].value_counts().to_dict())

@app.route("/api/project/discard_upload", methods=["POST"])
def api_discard_upload():
    c = CANDIDATES.pop(ACTIVE_PID, None)
    if c:
        try: os.remove(c["raw_path"])
        except Exception: pass
    return jsonify(ok=True)

# ---------------- formatted report export ----------------
from flask import Response
import reports
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
def _dl(data, mime, name):
    return Response(data, mimetype=mime, headers={"Content-Disposition": f'attachment; filename="{name}"'})

def _portfolio_risk(n=60):
    """Aggregate every risk_model flag across the top-N Tier-1 deals for the management report."""
    t1 = SCORED[SCORED["tier"] == "Tier 1 - Strong"].sort_values("total_score", ascending=False).head(n)
    results = [risk_model.assess(r, REFS, rate=float(ASSUMP["rate"]), ltv=float(ASSUMP["ltv"]), live_flood=False)
               for r in t1.to_dict("records")]
    import re as _re
    sev_order = ["Critical", "High", "Medium", "Low", "Minor", "Info"]
    sevc = {s: 0 for s in sev_order}; gradec = {}; agg = {}
    for res in results:
        gradec[res["grade"]] = gradec.get(res["grade"], 0) + 1
        for f in res["risks"]:
            sevc[f["severity"]] = sevc.get(f["severity"], 0) + 1
            title = _re.sub(r"\s*\([^)]*\)", "", f["title"]).strip()  # collapse deal-specific numbers
            key = (f["category"], title)
            a = agg.setdefault(key, {"title": title, "severity": f["severity"],
                "category": f["category"], "mitigation": f["mitigation"], "source": f["source"], "count": 0})
            a["count"] += 1
            if sev_order.index(f["severity"]) < sev_order.index(a["severity"]):
                a["severity"] = f["severity"]  # keep the worst severity seen for the group
    flags = sorted(agg.values(), key=lambda x: (sev_order.index(x["severity"]), -x["count"]))
    needs = [{"category": f["category"], "title": f["title"], "mitigation": f["mitigation"]}
             for f in flags if "needs" in f["source"]]
    avg = round(sum(r["score"] for r in results) / len(results), 1) if results else 0
    grade = ("A" if avg < 20 else "B" if avg < 38 else "C" if avg < 55 else "D" if avg < 72 else "F")
    return {"n": len(results), "avg_score": avg, "grade": grade, "severity_counts": sevc,
            "grade_dist": gradec, "flags": flags, "needs_report": needs}

@app.route("/api/report/portfolio.<fmt>")
def rep_portfolio(fmt):
    scen = request.args.get("scenario", "Base"); r = t_portfolio_dcf(scenario=scen)
    if fmt == "pdf":
        payload = {"dcf": r, "summary": t_market_summary(), "risk": _portfolio_risk(),
                   "scenario": scen, "project": projects.get_project(ACTIVE_PID).get("name", "Portfolio")}
        return _dl(reports.management_pdf(payload), "application/pdf", f"Terra_Investment_Committee_{scen}.pdf")
    if fmt == "xlsx": return _dl(reports.portfolio_xlsx(r, scen), XLSX, f"Terra_Portfolio_{scen}.xlsx")
    return jsonify(error="bad format"), 400

@app.route("/api/report/targets.xlsx")
def rep_targets():
    args = {"tier": request.args.get("tier", "Tier 1 - Strong"), "limit": int(request.args.get("limit", 200))}
    if request.args.get("state"): args["state"] = request.args["state"]
    return _dl(reports.targets_xlsx(t_search_targets(**args)["rows"], args["tier"]), XLSX, "Terra_Targets.xlsx")

@app.route("/api/report/property.pdf")
def rep_property():
    p = t_lookup_property(query=request.args.get("apn", ""))
    if not p.get("found"): return jsonify(error="not found"), 404
    apn = str(p["apn"]); ov = UW_OVERRIDES.get(apn, {}); a = _eff_assump(apn)
    price = ov.get("price", round(float(p["avm"]) * 0.9)); rent = ov.get("rent", p.get("market_rent") or 0)
    uw = U.underwrite(price, rent, a)
    rev = {tm: U.reverse_price(tm, tgt, a, rent) for tm, tgt in (("cap", 0.07), ("coc", 0.08), ("dscr", 1.25))}
    risk = risk_model.assess(p, REFS, rate=a["rate"], ltv=a["ltv"])
    return _dl(reports.property_pdf(p, uw, price=price, rent=rent, a=a, rev=rev, risk=risk),
               "application/pdf", "Terra_Property_%s.pdf" % apn[:18])

import xlmodel
def _prop_params(p):
    """Single-asset DCF params from the property + its effective (saved/override) assumptions."""
    apn = str(p.get("apn", "")); ov = UW_OVERRIDES.get(apn, {}); a = _eff_assump(apn)
    price = ov.get("price", float(p["avm"]) * 0.9) if p.get("avm") else ov.get("price", 0)
    rent = ov.get("rent", float(p["market_rent"]) if p.get("market_rent") else 0.0)
    return {**PORT_COMMON, "homes": 1, "price_home": price, "rent_home": rent,
            "tax": a["tax"], "ins_home": a["ins"], "rehab": a["rehab"], "acq": a.get("closing", PORT_COMMON["acq"]),
            "ltv": a["ltv"], "rate": a["rate"], "amort": a["amort"], "pm": a["pm"], "rm": a["maint"],
            "vacancy": a["vacancy"], "other_home": a["other"], "hoa_home": a["hoa"]}

@app.route("/api/report/underwriting_book.<fmt>")
def rep_uw_book(fmt):
    tier = request.args.get("tier", "Tier 1 - Strong"); limit = min(int(request.args.get("limit", 25)), 60)
    rows = t_search_targets(tier=tier, state=request.args.get("state"), limit=limit)["rows"]
    deals = []
    for p in rows:
        apn = str(p["apn"]); ov = UW_OVERRIDES.get(apn, {}); a = _eff_assump(apn)
        price = ov.get("price", round(float(p["avm"]) * 0.9)); rent = ov.get("rent", p.get("market_rent") or 0)
        uw = U.underwrite(price, rent, a)
        deals.append({"p": p, "price": price, "rent": rent, "a": a, "uw": uw})
    if not deals: return jsonify(error="no properties"), 404
    if fmt == "xlsx": return _dl(reports.underwriting_book_xlsx(deals), XLSX, "Terra_Underwriting_Book.xlsx")
    if fmt == "pdf": return _dl(reports.underwriting_book_pdf(deals), "application/pdf", "Terra_Underwriting_Book.pdf")
    return jsonify(error="bad format"), 400

@app.route("/api/report/model.xlsx")
def rep_model():
    p = t_lookup_property(query=request.args.get("apn", ""))
    if not p.get("found"): return jsonify(error="not found"), 404
    prof = projects.get_project(ACTIVE_PID)["profile"]
    loc = calc_trace._loc_values(p.get("zip"), REFS)
    tr = calc_trace.trace(p, REFS, prof, ASSUMP)
    fn = "Terra_APN_Model_%s.xlsx" % str(p.get("apn", "property"))[:20]
    return _dl(xlmodel.property_model(p, prof, ASSUMP, loc, tr, _prop_params(p), SCENARIOS), XLSX, fn)

# ---------------- live for-sale listings (RentCast / pluggable) ----------------
import listings
@app.route("/api/listings/status")
def listings_status(): return jsonify({"provider": listings.provider()})

@app.route("/api/listings/near")
def listings_near():
    try:
        lat = float(request.args["lat"]); lon = float(request.args["lon"])
    except Exception:
        return jsonify(error="lat & lon required"), 400
    return jsonify(listings.sale_near(lat, lon, float(request.args.get("radius", 3))))

import site_intel
@app.route("/api/site")
def api_site():
    try:
        lat = float(request.args["lat"]); lon = float(request.args["lon"])
    except Exception:
        return jsonify(error="lat & lon required"), 400
    return jsonify(site_intel.analyze(lat, lon))

import parcel
@app.route("/api/parcel")
def api_parcel():
    try:
        lat = float(request.args["lat"]); lon = float(request.args["lon"])
    except Exception:
        return jsonify(error="lat & lon required"), 400
    return jsonify(parcel.lookup(lat, lon))

@app.route("/api/massing")
def api_massing():
    a = request.args
    try:
        return jsonify(site_intel.massing(
            area_sf=float(a["area"]), use=a.get("use", "hotel"), shape=a.get("shape", "rectangular"),
            far=float(a.get("far", 2.0)), height_ft=float(a.get("height", 55)),
            lot_coverage=float(a.get("coverage", 0.45)), parking_ratio=float(a.get("parking", 1.0))))
    except Exception as e:
        return jsonify(error="area required (sq ft); " + str(e)[:80]), 400

@app.route("/api/listings.xlsx")
def listings_download():
    zc = request.args.get("zip", "").strip()
    if not zc: return jsonify(error="zip required"), 400
    res = listings.sale_by_zip(zc)
    if res.get("error") or not res.get("listings"):
        return jsonify(res), 400
    return _dl(reports.listings_xlsx(res["listings"], f"For-Sale · {zc}"), XLSX, f"Terra_ForSale_{zc}.xlsx")

# ---------------- auth gate (optional) ----------------
import collab, feedback
AUTH_HTML = """<!doctype html><meta charset=utf-8><title>{{mode}} · Terra</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&family=Fraunces:opsz,wght@9..144,600&display=swap" rel=stylesheet>
<style>:root{--emer:#0e9d6e}*{box-sizing:border-box}body{margin:0;height:100vh;display:grid;grid-template-columns:1.1fr .9fr;font-family:Inter,system-ui,sans-serif}
.hero{background:linear-gradient(160deg,#0b1320,#13233f);color:#fff;padding:54px 56px;display:flex;flex-direction:column;justify-content:center}
.hero .mk{width:46px;height:46px;border-radius:13px;background:linear-gradient(135deg,#0e9d6e,#13b87f);display:grid;place-items:center;color:#03120c;font-weight:800;font-size:22px;margin-bottom:24px}
.hero h1{font-family:Fraunces,serif;font-size:40px;margin:0 0 8px;line-height:1.1}
.hero p{color:#aeb8c9;font-size:15px;max-width:380px;line-height:1.6}
.hero ul{list-style:none;padding:0;margin:26px 0 0;color:#cdd5e3;font-size:13.5px}
.hero li{padding:6px 0;padding-left:24px;position:relative}.hero li:before{content:'✓';position:absolute;left:0;color:#13b87f;font-weight:800}
.right{display:grid;place-items:center;background:#f4f6fa}
.box{background:#fff;border:1px solid #e4e8f0;border-radius:18px;padding:34px;width:360px;box-shadow:0 18px 50px rgba(16,28,48,.12)}
h2{font-size:21px;margin:0 0 4px}.sub{color:#6b7689;font-size:13px;margin:0 0 20px}
input{width:100%;height:44px;border:1px solid #e4e8f0;border-radius:11px;padding:0 13px;margin-bottom:11px;font-size:14px;box-sizing:border-box;outline:none}
input:focus{border-color:var(--emer);box-shadow:0 0 0 3px rgba(14,157,110,.12)}
button{width:100%;height:46px;border:0;border-radius:11px;background:var(--emer);color:#fff;font-weight:700;font-size:14.5px;cursor:pointer}
.alt{text-align:center;margin-top:15px;font-size:13px;color:#6b7689}.alt a{color:var(--emer);font-weight:700;text-decoration:none}
.msg{font-size:13px;border-radius:9px;padding:9px 11px;margin-bottom:12px}
.err{background:#fdeceb;color:#b5362d}.ok{background:#e7f7f0;color:#0a7a55}</style>
<div class=hero><div class=mk>▲</div><h1>Terra</h1><p>Institutional acquisition intelligence — screen, score, underwrite and report across every property type.</p>
<ul><li>Deterministic engine · 100% Excel parity</li><li>Map, analytics, two-way underwriting</li><li>ATLAS copilot · team workspaces</li></ul></div>
<div class=right><div class=box>
<h2>{{ 'Create your account' if mode=='Sign up' else 'Welcome back' }}</h2><p class=sub>Terra · Acquisition Intelligence</p>
{% if err %}<div class="msg err">{{err}}</div>{% endif %}{% if ok %}<div class="msg ok">{{ok}}</div>{% endif %}
<form method=post action="{{ '/signup' if mode=='Sign up' else '/login' }}{{ '?next='+next if next else '' }}">
{% if mode=='Sign up' %}<input name=name placeholder="Full name">{% endif %}
<input name=email type=email placeholder=Email required autofocus>
<input name=password type=password placeholder="{{ 'Create a password (8+ chars)' if mode=='Sign up' else 'Password' }}" required>
<button>{{ mode }}</button></form>
<div class=alt>{% if mode=='Sign up' %}Already have an account? <a href="/login">Sign in</a>{% else %}New here? <a href="/signup">Create account</a>{% endif %}</div>
</div></div>"""

PUBLIC = {"/login", "/logout", "/signup", "/health"}
@app.before_request
def _gate():
    # CSRF: block cross-origin POSTs when auth is on
    if auth.enabled() and request.method == "POST" and request.path not in ("/login", "/signup"):
        origin = request.headers.get("Origin") or request.headers.get("Referer") or ""
        if origin and request.host not in origin:
            return jsonify(error="cross-origin blocked"), 403
    if not auth.enabled() or request.path in PUBLIC or request.path.startswith("/static"):
        return
    if not auth.current():
        if request.path.startswith("/api") or request.path == "/chat":
            return jsonify(error="auth required"), 401
        return redirect("/login?next=" + request.path)
    if request.path.startswith("/admin") and "admin" not in auth.current().get("caps", []):
        return ("Forbidden — admin only", 403)

@app.route("/login", methods=["GET", "POST"])
def login():
    nxt = request.args.get("next", "")
    if request.method == "POST":
        u = auth.verify(request.form.get("email"), request.form.get("password"))
        if u and "_status" in u:
            msg = "Your account is pending admin approval." if u["_status"] == "pending" else "Account disabled."
            return render_template_string(AUTH_HTML, mode="Sign in", err=msg, ok="", next=nxt), 403
        if u:
            session["user"] = u; return redirect(nxt or "/")
        return render_template_string(AUTH_HTML, mode="Sign in", err="Invalid credentials.", ok="", next=nxt), 401
    return render_template_string(AUTH_HTML, mode="Sign in", err="", ok="", next=nxt)

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        u, err = auth.register(request.form.get("email"), request.form.get("password"), request.form.get("name", ""))
        if err:
            return render_template_string(AUTH_HTML, mode="Sign up", err=err, ok="", next=""), 400
        if u["status"] == "active":
            session["user"] = {"email": u["name"] and request.form.get("email"), "name": u["name"], "role": u["role"], "caps": u["caps"]}
            return redirect("/")
        return render_template_string(AUTH_HTML, mode="Sign in", err="",
            ok="Account created — an admin will approve access shortly.", next=""), 200
    return render_template_string(AUTH_HTML, mode="Sign up", err="", ok="", next="")

@app.route("/logout")
def logout():
    session.pop("user", None); return redirect("/login")

# ---------------- user management (admin) ----------------
@app.route("/api/users")
def api_users():
    if auth.enabled() and "admin" not in (auth.current() or {}).get("caps", []): return jsonify(error="forbidden"), 403
    return jsonify(auth.list_users())

@app.route("/api/user/update", methods=["POST"])
def api_user_update():
    if auth.enabled() and "admin" not in (auth.current() or {}).get("caps", []): return jsonify(error="forbidden"), 403
    b = request.get_json(force=True)
    return jsonify({"ok": auth.set_user(b["email"], status=b.get("status"), role=b.get("role"))})

ADMIN_HTML = """<!doctype html><meta charset=utf-8><title>User Management · Terra</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&family=Fraunces:opsz,wght@9..144,600&display=swap" rel=stylesheet>
<style>*{box-sizing:border-box}body{margin:0;font-family:Inter,system-ui,sans-serif;background:#f4f6fa;color:#0b1320}
.top{background:#0b1320;color:#fff;padding:16px 28px;display:flex;align-items:center;gap:12px}
.top .mk{width:32px;height:32px;border-radius:9px;background:linear-gradient(135deg,#0e9d6e,#13b87f);display:grid;place-items:center;color:#03120c;font-weight:800}
.top b{font-family:Fraunces,serif;font-size:18px}.top a{margin-left:auto;color:#aeb8c9;text-decoration:none;font-size:13px}
.wrap{max-width:980px;margin:26px auto;padding:0 20px}h1{font-family:Fraunces,serif;font-size:24px}
.card{background:#fff;border:1px solid #e4e8f0;border-radius:14px;box-shadow:0 1px 3px rgba(16,28,48,.06);overflow:hidden}
table{width:100%;border-collapse:collapse;font-size:13.5px}th{background:#f7f9fc;text-align:left;padding:12px 14px;font-size:11px;text-transform:uppercase;color:#33405a;letter-spacing:.03em}
td{padding:12px 14px;border-top:1px solid #eef1f6}
.badge{padding:3px 9px;border-radius:999px;font-size:11px;font-weight:700}
.active{background:#e7f7f0;color:#0a7a55}.pending{background:#fdf2dd;color:#9a6510}.disabled{background:#eef1f6;color:#6b7689}
button{border:1px solid #e4e8f0;background:#fff;border-radius:8px;padding:6px 11px;font-size:12px;cursor:pointer;font-weight:600;margin-right:5px}
button:hover{border-color:#0e9d6e;color:#0a7a55}</style>
<div class=top><div class=mk>▲</div><b>Terra</b> <span style="color:#aeb8c9;font-size:13px">User Management</span><a href="/">← Back to app</a></div>
<div class=wrap><h1>Team & access</h1><p style="color:#6b7689">Approve sign-ups, set roles, and disable access. New sign-ups arrive as <b>pending</b>.</p>
<div class=card><table><thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Status</th><th>Created</th><th>Actions</th></tr></thead><tbody id=rows></tbody></table></div></div>
<script>
const post=(u,b)=>fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)}).then(r=>r.json());
async function load(){const us=await fetch('/api/users').then(r=>r.json());
 document.getElementById('rows').innerHTML=(us.length?us:[]).map(u=>`<tr><td><b>${u.name||''}</b></td><td>${u.email}</td><td>${u.role}</td>
  <td><span class="badge ${u.status}">${u.status}</span></td><td>${u.created||''}</td>
  <td>${u.status!=='active'?`<button onclick="upd('${u.email}',{status:'active'})">Approve</button>`:''}
   ${u.status!=='disabled'?`<button onclick="upd('${u.email}',{status:'disabled'})">Disable</button>`:`<button onclick="upd('${u.email}',{status:'active'})">Enable</button>`}
   ${u.role!=='Admin'?`<button onclick="upd('${u.email}',{role:'Admin'})">Make admin</button>`:`<button onclick="upd('${u.email}',{role:'Analyst'})">Make analyst</button>`}</td></tr>`).join('')
  ||'<tr><td colspan=6 style="color:#6b7689;text-align:center;padding:24px">No users yet. Sign-ups will appear here.</td></tr>';}
async function upd(email,patch){await post('/api/user/update',{email,...patch});load();}
load();
</script>"""

@app.route("/admin/users")
def admin_users(): return render_template_string(ADMIN_HTML)

# ---------------- discussions ----------------
@app.route("/api/thread")
def api_thread(): return jsonify(collab.get_thread(request.args.get("key", "")))

@app.route("/api/thread/post", methods=["POST"])
def api_thread_post():
    b = request.get_json(force=True)
    who = (auth.current() or {}).get("name", "you")
    return jsonify(collab.add_message(b["key"], who, b["text"]))

# ---------------- feedback ----------------
@app.route("/api/feedback", methods=["POST"])
def api_feedback():
    b = request.get_json(force=True)
    who = (auth.current() or {}).get("name", "anon")
    return jsonify(feedback.add(b.get("rating", "up"), b.get("comment", ""), b.get("context", ""), who))

@app.route("/api/feedback/summary")
def api_feedback_summary():
    if auth.enabled() and "admin" not in (auth.current() or {}).get("caps", []): return jsonify(error="forbidden"), 403
    return jsonify(feedback.summary())

# ---------------- routes ----------------
@app.route("/health")
def health(): return jsonify(status="ok", rows=len(SCORED), model=assistant.MODEL, auth=auth.enabled(),
                             atlas_ai=bool(os.environ.get("ANTHROPIC_API_KEY")),
                             listings=bool(os.environ.get("RENTCAST_API_KEY")))

@app.route("/")
def index(): return render_template("index.html")

@app.route("/api/<tool>", methods=["POST"])
def api(tool):
    if tool not in DISPATCH: return jsonify(error="unknown tool"), 404
    args = request.get_json(silent=True) or {}
    try:
        return jsonify(DISPATCH[tool](**args))
    except Exception as e:
        return jsonify(error=str(e)), 400

@app.route("/chat", methods=["POST"])
def chat():
    body = request.get_json(force=True)
    prof = projects.get_project(ACTIVE_PID)["profile"]
    snap = t_market_summary()
    snap["rows"] = len(SCORED)
    out = assistant.ask(body.get("message", ""), body.get("history", []),
                        DISPATCH, snap, profile=prof, assumptions=ASSUMP)
    return jsonify(out)

if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
