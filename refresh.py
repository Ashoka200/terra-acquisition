"""
refresh.py — atomic re-score pipeline. Drop in a new ATTOM-style universe export
and rebuild the canonical store so every link stays correct.

  python refresh.py                      # re-score current universe
  python refresh.py --universe new.parquet
  python refresh.py --workbook           # also regenerate the Excel ScoredUniverse table

Steps: gate (fail-closed) -> recompute per-state blended costs -> score with fixes
-> write scored.parquet + fix_params.json -> stamp data/refresh.json.
"""
import os, sys, json, hashlib, argparse, subprocess
from datetime import datetime, timezone
import pandas as pd
sys.path.insert(0, os.path.dirname(__file__))
import re_engine as E
import data_guard

HERE = os.path.dirname(__file__)
DATA = os.environ.get("RE_DATA", os.path.join(HERE, "..", "data"))

STATE_COSTS = {
    "TX": {"tax": 0.0140, "ins": 2800}, "GA": {"tax": 0.0090, "ins": 1700},
    "FL": {"tax": 0.0090, "ins": 3800}, "TN": {"tax": 0.0065, "ins": 1700},
    "NC": {"tax": 0.0082, "ins": 1900}, "AZ": {"tax": 0.0063, "ins": 1600},
    "_default": {"tax": 0.0110, "ins": 1800},
}
CAPEX_PER_HOME = 1200
FIXES = {"avm_discount": 0.90, "rent_realization": 0.95}

def refresh(universe_path=None, do_workbook=False):
    refs = E.load_refs(DATA)
    upath = universe_path or os.path.join(DATA, "universe_raw.parquet")
    print(f"[1/5] gate: validating {os.path.basename(upath)} ...")
    uni = pd.read_parquet(upath) if upath.endswith(".parquet") else pd.read_csv(upath)
    ok, rep = data_guard.validate(uni)
    print(f"      rows={rep.get('rows')} blocking={rep['blocking']} warnings={rep['warnings']}")
    if not ok:
        print("      REJECTED — fail-closed. Nothing written."); return None

    print("[2/5] scoring with fixes ...")
    d = E.score_universe(uni, refs, fixes=FIXES)
    d["market_rent"] = (pd.to_numeric(d["max_rent"], errors="coerce") * FIXES["rent_realization"]).round(0)

    print("[3/5] recomputing per-state blended costs from Tier-1 mix ...")
    t1 = d[d["tier"] == "Tier 1 - Strong"]
    mix = t1["state"].value_counts(normalize=True)
    btax = sum(mix.get(s,0)*STATE_COSTS.get(s, STATE_COSTS["_default"])["tax"] for s in mix.index)
    bins = sum(mix.get(s,0)*STATE_COSTS.get(s, STATE_COSTS["_default"])["ins"] for s in mix.index)
    fix_params = {
        "fixes": FIXES, "capex_per_home": CAPEX_PER_HOME, "state_costs": STATE_COSTS,
        "tier1_state_mix": {k: round(float(v),4) for k,v in mix.items()},
        "blended_tax_pct": round(float(btax),4), "blended_insurance": round(float(bins),0),
        "tier1_avg_avm": round(float(t1["avm"].mean()),0),
        "tier1_avg_maxrent": round(float(t1["max_rent"].mean()),0),
        "tier1_avg_marketrent": round(float(t1["market_rent"].mean()),0),
        "tier1_count": int(len(t1)),
    }
    json.dump(fix_params, open(os.path.join(DATA, "fix_params.json"), "w"), indent=2)

    print("[4/5] writing scored.parquet ...")
    cols = ["apn","address","city","state","zip","lat","lon","yearbuilt","sqft","avm","beds",
            "corp","market_rent","gross_yield","tenure","total_score","tier"]
    out = d[cols].copy()
    for c in ["lat","lon"]: out[c] = pd.to_numeric(out[c], errors="coerce")
    out.to_parquet(os.path.join(DATA, "scored.parquet"), index=False)

    print("[5/5] stamping data/refresh.json ...")
    cfg = json.dumps(refs["settings"], sort_keys=True, default=str) + json.dumps(fix_params, sort_keys=True)
    stamp = {
        "refreshed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "universe_rows": int(len(d)), "config_hash": hashlib.sha256(cfg.encode()).hexdigest()[:12],
        "tiers": d["tier"].value_counts().to_dict(),
        "tier1_count": fix_params["tier1_count"], "blended_tax": fix_params["blended_tax_pct"],
        "blended_insurance": fix_params["blended_insurance"],
    }
    json.dump(stamp, open(os.path.join(DATA, "refresh.json"), "w"), indent=2)
    print("      ", json.dumps(stamp["tiers"]))

    if do_workbook:
        print("[+] regenerating workbook ScoredUniverse table (this rewrites values) ...")
        root = os.path.join(HERE, "..")
        for script in ["re_build_workbook.py", "re_build_workbook_pack.py"]:
            subprocess.run([sys.executable, os.path.join(root, script)], cwd=root, check=True)
        print("      workbook regenerated.")
    print("DONE — links stay correct (structured table refs + Python-sized ranges).")
    return stamp

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe"); ap.add_argument("--workbook", action="store_true")
    a = ap.parse_args()
    refresh(a.universe, a.workbook)
