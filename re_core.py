"""
re_core.py — config-driven, multi-property-type acquisition engine.

The scoring model is now DATA, not code:
  * a PROFILE (per property type) declares the gate, the metric registry
    (label/pillar/weight/normalization), the risk overlay, tiers, and assumptions.
  * `prepare()` (one small function per type) builds the derived columns + lookups.
  * `score()` is generic: it runs ANY profile's gate + metrics + risk + tiers.

Users can reweight, toggle, retune bands, or ADD a metric (pointing at any existing
column with a chosen normalization) WITHOUT touching engine code.

SFR is expressed as a profile and re-proven to tie to the original Excel scores.
"""
import numpy as np, pandas as pd

# ----------------------------------------------------------- generic normalizers
def clamp01(x): return np.clip(x, 0.0, 1.0)

def _norm(kind, v, p, match):
    """Return a 0-100 sub-score array. `v` is the metric's input column (np array)."""
    v = pd.to_numeric(pd.Series(v), errors="coerce").to_numpy() if kind not in ("binary",) else v
    if kind == "band":            # higher value, scaled lo->hi
        s = clamp01((v - p["lo"]) / (p["hi"] - p["lo"])) * 100
        s = np.where(np.isnan(v), 0.0, s)
    elif kind == "band_inv":      # lower value is better (e.g. price)
        s = clamp01((p["hi"] - v) / (p["hi"] - p["lo"])) * 100
        s = np.where(np.isnan(v), 0.0, s)
    elif kind == "triangular":    # closeness to a center
        s = np.maximum(0, 1 - np.abs(v - p["center"]) / p["half"]) * 100
        s = np.where(np.isnan(v), 0.0, s)
    elif kind == "ratio_cap":     # v / cap, missing -> default
        s = clamp01(v / p["cap"]) * 100
        s = np.where(np.isnan(v), p.get("missing", 50.0), s)
    elif kind == "binary":        # ==value -> 100 else 0
        s = np.where(np.asarray(v) == p["value"], 100.0, 0.0)
    elif kind == "passthrough":   # already 0-100 (lookups computed in prepare)
        s = np.where(np.isnan(v), 0.0, v)
    else:
        raise ValueError("unknown norm kind: " + kind)
    return np.where(match, s, 0.0)

def _gate(df, conds):
    m = np.ones(len(df), dtype=bool)
    for c in conds:
        col = pd.to_numeric(df[c["field"]], errors="coerce") if c["op"] in ("between","ge","le") else df[c["field"]]
        if c["op"] == "eq":        m &= (df[c["field"]] == c["value"]).to_numpy()
        elif c["op"] == "between": m &= ((col >= c["lo"]) & (col <= c["hi"])).to_numpy()
        elif c["op"] == "ge":      m &= (col >= c["value"]).to_numpy()
        elif c["op"] == "le":      m &= (col <= c["value"]).to_numpy()
        elif c["op"] == "truthy":  m &= (df[c["field"]] == 1).to_numpy()
    return m

# ----------------------------------------------------------- generic scorer
def score(prepared, profile):
    """prepared = df with all metric input columns + 'match' + 'risk_sc'. Returns scored df."""
    d = prepared
    match = d["match"].to_numpy().astype(bool)
    metrics = [m for m in profile["metrics"] if m.get("weight", 0) and m.get("on", True)]
    Wtot = sum(m["weight"] for m in metrics) or 1
    acc = np.zeros(len(d))
    for m in metrics:
        sub = _norm(m["norm"]["kind"], d[m["input"]].to_numpy(), m["norm"], match)
        d[m["key"] + "_sc"] = sub
        acc += sub * m["weight"]
    raw = acc / Wtot
    r = profile["risk"]
    risk = d["risk_sc"].to_numpy()
    haircut = 1 - np.maximum(0, risk - r["baseline"]) / (100 - r["baseline"]) * r["sensitivity"]
    total = np.where(match, raw * haircut, 0.0)
    d["total_score"] = total
    t1, t2 = profile["tiers"]["tier1"], profile["tiers"]["tier2"]
    d["tier"] = np.where(~match, "Not a Match",
                np.where(total >= t1, "Tier 1 - Strong",
                np.where(total >= t2, "Tier 2 - Moderate", "Tier 3 - Watch")))
    return d

# ----------------------------------------------------------- SFR profile + prepare
def best_bed_cutoff(deal_history):
    """Data-calibrated sqft cutoff (max accuracy vs real beds in the deal book)."""
    dh = deal_history.dropna(subset=["sqft", "beds"]).copy()
    dh["sqft"] = pd.to_numeric(dh["sqft"], errors="coerce"); dh["beds"] = pd.to_numeric(dh["beds"], errors="coerce")
    best, bestc = -1, 1789
    for c in range(1200, 2400, 10):
        acc = (np.where(dh["sqft"] < c, 3, 4) == dh["beds"]).mean()
        if acc > best: best, bestc = acc, c
    return bestc, round(best, 3)

def build_sfr_profile(refs, fixes):
    if isinstance(fixes, dict) and "fixes" in fixes:  # tolerate full fix_params.json
        fixes = fixes["fixes"]
    s = refs["settings"]; bb, sc = s["buybox"], s["scoring"]
    f = lambda x: float(x)
    return {
        "key": "sfr", "label": "Single-Family Rental", "property_type": "SFR",
        "fixes": fixes,
        "gate": [
            {"field": "proptype", "op": "eq", "value": bb["proptype"]},
            {"field": "yearbuilt", "op": "between", "lo": f(bb["yearbuilt_low"]), "hi": f(bb["yearbuilt_high"])},
            {"field": "sqft", "op": "between", "lo": f(bb["sqft_low"]), "hi": f(bb["sqft_high"])},
            {"field": "avm", "op": "between", "lo": f(bb["price_low"]), "hi": f(bb["price_high"])},
            {"field": "approved", "op": "truthy"},
        ],
        "metrics": [
            {"key": "yield", "label": "Gross Yield", "pillar": "Return", "weight": f(sc["w_yield"]),
             "input": "gross_yield", "norm": {"kind": "band", "lo": f(bb["yield_floor"]), "hi": f(bb["yield_target"])}},
            {"key": "price", "label": "Price / Margin", "pillar": "Return", "weight": f(sc["w_price"]),
             "input": "avm", "norm": {"kind": "band_inv", "lo": f(bb["price_low"]), "hi": f(bb["price_high"])}},
            {"key": "abs", "label": "Absentee / Corporate", "pillar": "Motivation", "weight": f(sc["w_absentee"]),
             "input": "corp", "norm": {"kind": "binary", "value": "Y"}},
            {"key": "ten", "label": "Tenure / Equity", "pillar": "Motivation", "weight": f(sc["w_tenure"]),
             "input": "tenure", "norm": {"kind": "ratio_cap", "cap": f(sc["tenure_saturation"]), "missing": 50.0}},
            {"key": "proven", "label": "Proven Market", "pillar": "Location", "weight": f(sc["w_proven"]),
             "input": "proven_v", "norm": {"kind": "passthrough"}},
            {"key": "density", "label": "Cluster Density", "pillar": "Location", "weight": f(sc["w_density"]),
             "input": "density_v", "norm": {"kind": "passthrough"}},
            {"key": "target", "label": "Market Target", "pillar": "Location", "weight": f(sc["w_target"]),
             "input": "target_v", "norm": {"kind": "passthrough"}},
            {"key": "momentum", "label": "Rent Momentum", "pillar": "Location", "weight": f(sc["w_momentum"]),
             "input": "momentum_v", "norm": {"kind": "passthrough"}},
            {"key": "sqftfit", "label": "Sqft Fit", "pillar": "Fit", "weight": f(sc["w_sqft"]),
             "input": "sqft", "norm": {"kind": "triangular",
                "center": (f(bb["sqft_low"]) + f(bb["sqft_high"])) / 2, "half": (f(bb["sqft_high"]) - f(bb["sqft_low"])) / 2}},
            {"key": "yearfit", "label": "Year-Built Fit", "pillar": "Fit", "weight": f(sc["w_year"]),
             "input": "yearbuilt", "norm": {"kind": "band", "lo": f(bb["yearbuilt_low"]), "hi": f(bb["yearbuilt_high"])}},
        ],
        "risk": {"sensitivity": f(sc["risk_sensitivity"]), "baseline": f(sc["risk_baseline"]), "source": "market_risk"},
        "tiers": {"tier1": f(sc["tier1"]), "tier2": f(sc["tier2"])},
        "bed_cutoff": f(bb["bed_cutoff"]), "asof_serial": f(sc["asof_serial"]),
        "underwriter": "sfr_rental",
        "assumptions": {"avm_discount": fixes["avm_discount"], "rent_realization": fixes["rent_realization"],
                        "vacancy": 0.05, "pm": 0.08, "maint": 0.05, "capex_per_home": 1200,
                        "ltv": 0.70, "rate": 0.0725, "amort": 30, "io_years": 5, "hold": 7, "exit_cap": 0.065},
    }

def prepare_sfr(df, profile, refs, use_real_beds=True):
    fixes = profile["fixes"]; rr = refs["rent"]
    d = df.copy()
    d["zip"] = d["zip"].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
    for c in ["yearbuilt", "sqft", "avm", "sale_serial"]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    # BEDS: real if present + asked, else data-calibrated estimate (flagged)
    cutoff = profile["bed_cutoff"]
    if use_real_beds and "beds" in d.columns and pd.to_numeric(d["beds"], errors="coerce").notna().mean() > 0.5:
        d["beds"] = pd.to_numeric(d["beds"], errors="coerce")
        d["beds_source"] = "actual"
    else:
        d["beds"] = np.where(d["sqft"].isna(), np.nan, np.where(d["sqft"] < cutoff, 3, 4))
        d["beds_source"] = "estimated(sqft)"
    approved_zips = set(rr["zip"].dropna())
    d["approved"] = d["zip"].isin(approved_zips).astype(int)
    rent_key = {(r.zip, int(r.beds)): r.max_rent for r in rr.dropna(subset=["beds", "max_rent"]).itertuples()}
    def lk(zp, bd, a):
        if a != 1 or pd.isna(bd): return np.nan
        bd = int(bd); return rent_key.get((zp, bd), rent_key.get((zp, 4 if bd == 3 else 3)))
    maxrent = np.array([lk(z, b, a) for z, b, a in zip(d["zip"], d["beds"], d["approved"])])
    d["market_rent"] = maxrent * fixes["rent_realization"]
    basis = d["avm"].to_numpy() * fixes["avm_discount"]
    with np.errstate(divide="ignore", invalid="ignore"):
        d["gross_yield"] = np.where(np.isnan(d["market_rent"]) | (basis == 0), np.nan, d["market_rent"] * 12 / basis)
    d["tenure"] = np.where(d["sale_serial"].isna() | (d["sale_serial"] == 0), np.nan,
                           (profile["asof_serial"] - d["sale_serial"]) / 365.25)
    # location lookups
    zip2msa = rr.dropna(subset=["msa"]).drop_duplicates("zip").set_index("zip")["msa"].to_dict()
    msa2risk = refs["risk"].dropna(subset=["risk"]).set_index("msa")["risk"].to_dict()
    zs = refs["zip_stats"].drop_duplicates("zip").set_index("zip")
    for src, col in [("proven", "proven_v"), ("density", "density_v"), ("target", "target_v"), ("momentum", "momentum_v")]:
        dd = zs[src].to_dict(); d[col] = [dd.get(z, 0) or 0 for z in d["zip"]]
    d["risk_sc_raw"] = [msa2risk.get(zip2msa.get(z), 0) or 0 for z in d["zip"]]
    # gate + risk gating
    d["match"] = _gate(d, profile["gate"]).astype(int)
    m = d["match"] == 1
    d["risk_sc"] = np.where(m, d["risk_sc_raw"], 0.0)
    for col in ["proven_v", "density_v", "target_v", "momentum_v"]:
        d[col] = np.where(m, d[col], 0.0)
    return d

REGISTRY = {"SFR": {"build": build_sfr_profile, "prepare": prepare_sfr}}

def run(df, refs, fixes, ptype="SFR", profile=None, use_real_beds=True):
    reg = REGISTRY[ptype]
    profile = profile or reg["build"](refs, fixes)
    prepared = reg["prepare"](df, profile, refs, use_real_beds=use_real_beds)
    return score(prepared, profile), profile


if __name__ == "__main__":
    import re_engine as E, json, os
    DATA = os.path.join(os.path.dirname(__file__), "..", "data")
    refs = E.load_refs(DATA); FX = json.load(open(os.path.join(DATA, "fix_params.json")))
    uni = pd.read_parquet(os.path.join(DATA, "universe_raw.parquet"))
    # parity: re_core SFR (no fixes) must tie to original cached scores
    base, prof = run(uni, refs, {"avm_discount": 1.0, "rent_realization": 1.0})
    print("=== re_core SFR PARITY vs Excel cached ===")
    print("match:", int(base["match"].sum()), "(want 284932)")
    print("tiers:", base["tier"].value_counts().to_dict())
    d = (pd.to_numeric(base.loc[base["match"]==1,"total_score"]) - pd.to_numeric(uni.loc[base["match"]==1,"x_total"])).abs().max()
    print("total_score max abs diff:", d)
    print("tier agreement:", (base["tier"].values == uni["x_tier"].astype(str).values).mean())
    c, acc = best_bed_cutoff(refs["deal_history"])
    print(f"\nbest data-calibrated bed cutoff: {c} (acc {acc*100:.1f}% vs 1789=73.8%)")
