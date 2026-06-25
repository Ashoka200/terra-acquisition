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

# numpy-safe JSON (pandas/iloc returns np.int64/float64 which Flask can't serialize)
import numpy as np
from flask.json.provider import DefaultJSONProvider
class _NPJSON(DefaultJSONProvider):
    def default(self, o):
        if isinstance(o, np.integer): return int(o)
        if isinstance(o, np.floating): return None if np.isnan(o) else float(o)
        if isinstance(o, np.ndarray): return o.tolist()
        return super().default(o)
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

PORT_COMMON = dict(homes=100, acq=0.02, rehab=5000, rent_growth=0.03, exp_growth=0.025,
                   vacancy=0.05, pm=0.08, rm=0.05, other_home=300, hoa_home=0,
                   ltv=0.70, rate=0.0725, amort=30, io_years=5, hold=7, exit_cap=0.065,
                   selling=0.02, loan_fee=0.01,
                   price_home=FX["tier1_avg_avm"]*FX["fixes"]["avm_discount"],
                   rent_home=FX["tier1_avg_marketrent"], tax=FX["blended_tax_pct"],
                   ins_home=int(FX["blended_insurance"]), capex_home=FX["capex_per_home"])

# ---------------- engine tools (also the chatbot's tools) ----------------
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
    if state: q = q[q["state"] == state.upper()]
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

def t_underwrite(price, monthly_rent, rate=None, ltv=None):
    a = dict(ASSUMP);
    if rate is not None: a["rate"] = rate
    if ltv is not None: a["ltv"] = ltv
    return U.underwrite(price, monthly_rent, a)

def t_reverse_solve(target_metric, target, monthly_rent, rate=None, ltv=None):
    a = dict(ASSUMP)
    if rate is not None: a["rate"] = rate
    if ltv is not None: a["ltv"] = ltv
    P = U.reverse_price(target_metric, target, a, monthly_rent)
    return {"implied_price": round(P, 0), "verify": U.underwrite(P, monthly_rent, a)}

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
    if state: q = q[q["state"] == state.upper()]
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

DISPATCH = {"market_summary": t_market_summary, "search_targets": t_search_targets,
            "lookup_property": t_lookup_property, "underwrite": t_underwrite,
            "reverse_solve": t_reverse_solve, "portfolio_dcf": t_portfolio_dcf,
            "map_points": t_map_points, "analytics": t_analytics,
            "geocode": t_geocode, "site_analysis": t_site_analysis, "massing": t_massing_tool}

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

# ---------------- formatted report export ----------------
from flask import Response
import reports
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
def _dl(data, mime, name):
    return Response(data, mimetype=mime, headers={"Content-Disposition": f'attachment; filename="{name}"'})

@app.route("/api/report/portfolio.<fmt>")
def rep_portfolio(fmt):
    scen = request.args.get("scenario", "Base"); r = t_portfolio_dcf(scenario=scen)
    if fmt == "pdf": return _dl(reports.portfolio_pdf(r, scen), "application/pdf", f"Terra_Portfolio_{scen}.pdf")
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
    uw = t_underwrite(p["avm"] * 0.9, p["market_rent"])
    return _dl(reports.property_pdf(p, uw), "application/pdf", "Terra_Property.pdf")

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
    out = assistant.ask(body.get("message", ""), body.get("history", []),
                        DISPATCH, t_market_summary())
    return jsonify(out)

if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
