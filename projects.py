"""
projects.py — multi-project / multi-type workspace. Each project = a property-type
profile (auto-loaded defaults: gate, scoring, assumptions) + its own data + a column
mapping (so ANY source schema maps to the engine's canonical fields) + editable
overrides. Pick a type -> the buy box / scoring / assumptions update automatically.
"""
import os, json, time, hashlib
import pandas as pd
import re_core, profiles, data_guard

HERE = os.path.dirname(__file__)
DATA = os.environ.get("RE_DATA", os.path.join(HERE, "..", "data"))
PROJ_DIR = os.path.join(DATA, "projects")
os.makedirs(PROJ_DIR, exist_ok=True)

# canonical fields the engine expects, per type (for the mapping wizard)
CANONICAL = {
    "SFR":  ["apn", "address", "city", "state", "zip", "lat", "lon", "proptype",
             "yearbuilt", "sqft", "avm", "sale_serial", "corp", "beds?"],
    "MF":   ["id", "address", "city", "state", "zip", "lat", "lon", "proptype", "units",
             "yearbuilt", "price", "noi?", "gsr?", "occupancy?", "rent_per_unit?"],
    "FLIP": ["id", "address", "city", "state", "zip", "lat", "lon", "sqft", "yearbuilt",
             "arv", "price", "rehab_est?", "days_on_market?", "ppsf?"],
    "LAND": ["id", "address", "city", "state", "zip", "lat", "lon", "acres", "price",
             "zoning?", "growth_index?", "buildability?", "entitlement?"],
}

def _slug(s):
    return "".join(c if c.isalnum() else "-" for c in s.lower()).strip("-")[:40] or "project"

def _path(pid): return os.path.join(PROJ_DIR, pid, "project.json")

def list_projects():
    out = []
    for pid in sorted(os.listdir(PROJ_DIR)):
        p = _path(pid)
        if os.path.exists(p):
            d = json.load(open(p)); out.append({k: d[k] for k in ("id", "name", "type", "created", "rows")})
    return out

def get_project(pid):
    p = _path(pid)
    return json.load(open(p)) if os.path.exists(p) else None

def save_project(d):
    os.makedirs(os.path.join(PROJ_DIR, d["id"]), exist_ok=True)
    json.dump(d, open(_path(d["id"]), "w"), indent=2, default=str)
    return d

def create_project(name, ptype, refs=None, fixes=None, source_path=None, column_map=None, rows=0):
    pid = _slug(name)
    prof = profiles.get_profile(ptype, refs, fixes)
    d = {"id": pid, "name": name, "type": ptype, "created": time.strftime("%Y-%m-%d"),
         "profile": prof, "column_map": column_map or {}, "source": source_path,
         "assumptions": prof.get("assumptions", {}), "rows": rows}
    return save_project(d)

def _num(x):
    try: return float(x)
    except (TypeError, ValueError): return x

def update_profile(pid, patch):
    """patch may include metrics (update or add), gate, tiers, risk, assumptions."""
    d = get_project(pid)
    if not d: return None
    prof = d["profile"]
    if "metrics" in patch:
        by = {m["key"]: m for m in prof["metrics"]}
        for upd in patch["metrics"]:
            k = upd.get("key")
            if k in by:
                if "weight" in upd: by[k]["weight"] = float(upd["weight"])
                if "on" in upd: by[k]["on"] = bool(upd["on"])
            elif upd.get("label") and upd.get("input") and upd.get("norm"):  # brand-new metric
                upd["weight"] = float(upd.get("weight", 0)); upd["on"] = True
                prof["metrics"].append(upd)
    if "add_metric" in patch:
        prof["metrics"].append(patch["add_metric"])
    if "gate" in patch:
        gby = {g["field"]: g for g in prof["gate"]}
        for upd in patch["gate"]:
            g = gby.get(upd.get("field"))
            if not g: continue
            for f in ("lo", "hi", "value"):
                if f in upd and f in g: g[f] = _num(upd[f])
    if "tiers" in patch:
        prof["tiers"].update({k: float(v) for k, v in patch["tiers"].items()})
    if "risk" in patch:
        prof["risk"].update({k: float(v) for k, v in patch["risk"].items()})
    if "assumptions" in patch:
        clean = {k: _num(v) for k, v in patch["assumptions"].items()}
        d["assumptions"].update(clean); prof.setdefault("assumptions", {}).update(clean)
    return save_project(d)

def apply_map(df, column_map):
    if not column_map: return df
    inv = {v: k for k, v in column_map.items() if v}
    return df.rename(columns=inv)

def load_scored(pid, refs=None):
    """Return a scored DataFrame for the project (canonical display columns)."""
    d = get_project(pid)
    if not d: raise ValueError("no such project")
    cache = os.path.join(PROJ_DIR, pid, "scored.parquet")
    if os.path.exists(cache):
        return pd.read_parquet(cache)
    # SFR default project -> reuse the master scored.parquet
    if d["type"] == "SFR" and not d.get("source"):
        return pd.read_parquet(os.path.join(DATA, "scored.parquet"))
    # score a provided source via re_core
    raw = pd.read_parquet(d["source"]) if d["source"].endswith(".parquet") else pd.read_csv(d["source"])
    raw = apply_map(raw, d["column_map"])
    scored, _ = re_core.run(raw, refs, d["profile"].get("fixes", {"avm_discount":0.9,"rent_realization":0.95}),
                            ptype=d["type"], profile=d["profile"])
    scored.to_parquet(cache, index=False)
    return scored

def ensure_default(refs, fixes):
    """First-run: register the current SFR universe as a project."""
    pid = "sfr-sun-belt"
    if not get_project(pid):
        prof = profiles.get_profile("SFR", refs, fixes)
        n = len(pd.read_parquet(os.path.join(DATA, "scored.parquet")))
        save_project({"id": pid, "name": "SFR · Sun Belt", "type": "SFR",
                      "created": time.strftime("%Y-%m-%d"), "profile": prof,
                      "column_map": {}, "source": None, "assumptions": {}, "rows": n})
    return pid


if __name__ == "__main__":
    import re_engine as E
    refs = E.load_refs(DATA); FX = json.load(open(os.path.join(DATA, "fix_params.json")))
    pid = ensure_default(refs, FX)
    print("default project:", pid)
    print("projects:", [p["name"] for p in list_projects()])
    print("canonical SFR fields:", CANONICAL["SFR"])
    # demo: spin up a Flip project + edit a weight
    create_project("Demo Flips", "FLIP", refs, FX)
    update_profile("demo-flips", {"metrics": [{"key": "spread", "weight": 45}]})
    p = get_project("demo-flips")
    print("flip spread weight now:", [m for m in p["profile"]["metrics"] if m["key"]=="spread"][0]["weight"])
    print("flip assumptions:", p["assumptions"])
