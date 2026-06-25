"""
re_engine.py — United Brothers Residential Acquisition Engine (Python port of the
Excel 'Potential Targets' model). Single source of truth for scoring + DCF.

Design:
  * score_universe()  — gate + 4-pillar score + risk haircut + tiering (ports D4)
  * FIXES are config-driven and OFF by default so we can prove parity first,
    then flip them on.
"""
import json, math
import numpy as np
import pandas as pd

# ---------------------------------------------------------------- helpers
def _zip(s):
    if s is None: return None
    s = str(s).strip()
    if s.endswith(".0"): s = s[:-2]
    return s

def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return np.nan

def clamp01(x):
    return np.clip(x, 0.0, 1.0)

# ---------------------------------------------------------------- config
DEFAULT_FIXES = {
    "avm_discount": 1.00,        # FIX#1: basis = avm * discount (e.g. 0.90). 1.0 = off
    "rent_realization": 1.00,    # FIX#2: market rent = max_rent * factor (e.g. 0.92). 1.0 = off
}

# ---------------------------------------------------------------- load refs
def load_refs(data_dir):
    rr = pd.read_csv(f"{data_dir}/rent_reference.csv", dtype=str)
    rr["zip"] = rr["zip"].map(_zip)
    rr["beds"] = pd.to_numeric(rr["beds"], errors="coerce").astype("Int64")
    rr["max_rent"] = pd.to_numeric(rr["max_rent"], errors="coerce")
    zs = pd.read_csv(f"{data_dir}/zip_stats.csv", dtype=str)
    zs["zip"] = zs["zip"].map(_zip)
    for c in ["proven","density","target","momentum"]:
        zs[c] = pd.to_numeric(zs[c], errors="coerce")
    mr = pd.read_csv(f"{data_dir}/market_risk.csv", dtype=str)
    mr["risk"] = pd.to_numeric(mr["risk"], errors="coerce")
    dh = pd.read_csv(f"{data_dir}/deal_history.csv")
    settings = json.load(open(f"{data_dir}/settings.json"))
    return {"rent": rr, "zip_stats": zs, "risk": mr, "deal_history": dh, "settings": settings}

# ---------------------------------------------------------------- scoring
def score_universe(df, refs, fixes=None):
    fixes = {**DEFAULT_FIXES, **(fixes or {})}
    s = refs["settings"]
    bb, sc = s["buybox"], s["scoring"]

    yb_lo, yb_hi = float(bb["yearbuilt_low"]), float(bb["yearbuilt_high"])
    sf_lo, sf_hi = float(bb["sqft_low"]), float(bb["sqft_high"])
    pr_lo, pr_hi = float(bb["price_low"]), float(bb["price_high"])
    cutoff = float(bb["bed_cutoff"])
    yfloor, ytarget = float(bb["yield_floor"]), float(bb["yield_target"])
    asof = float(sc["asof_serial"]); sat = float(sc["tenure_saturation"])
    W = {k: float(sc[k]) for k in ["w_yield","w_price","w_absentee","w_tenure",
         "w_proven","w_density","w_target","w_momentum","w_sqft","w_year"]}
    Wtot = sum(W.values())
    t1, t2 = float(sc["tier1"]), float(sc["tier2"])
    rsens, rbase = float(sc["risk_sensitivity"]), float(sc["risk_baseline"])

    # ---- lookups
    rr = refs["rent"]
    approved_zips = set(rr["zip"].dropna())
    rent_key = {(r.zip, int(r.beds)): r.max_rent
                for r in rr.dropna(subset=["beds","max_rent"]).itertuples()}
    zip2msa = (rr.dropna(subset=["msa"]).drop_duplicates("zip")
               .set_index("zip")["msa"].to_dict())
    msa2risk = refs["risk"].dropna(subset=["risk"]).set_index("msa")["risk"].to_dict()
    zs = refs["zip_stats"].drop_duplicates("zip").set_index("zip")
    proven_d = zs["proven"].to_dict(); density_d = zs["density"].to_dict()
    target_d = zs["target"].to_dict(); momentum_d = zs["momentum"].to_dict()

    d = df.copy()
    d["zip"] = d["zip"].map(_zip)
    for c in ["yearbuilt","sqft","avm","sale_serial"]:
        d[c] = pd.to_numeric(d[c], errors="coerce")

    # ---- AE beds
    beds = np.where(d["sqft"].isna(), np.nan, np.where(d["sqft"] < cutoff, 3, 4))
    d["beds"] = beds

    # ---- AF approved zip
    d["approved"] = d["zip"].isin(approved_zips).astype(int)

    # ---- AG max rent (zip+beds, fallback to other bucket); FIX#2 realization
    def lookup_rent(zp, bd):
        if pd.isna(bd): return np.nan
        bd = int(bd)
        v = rent_key.get((zp, bd))
        if v is None:
            v = rent_key.get((zp, 4 if bd == 3 else 3))
        return v
    maxrent = np.array([lookup_rent(z, b) if a == 1 else np.nan
                        for z, b, a in zip(d["zip"], d["beds"], d["approved"])])
    d["max_rent"] = maxrent
    rent_used = maxrent * fixes["rent_realization"]

    # ---- AH gross yield (FIX#1 basis discount on AVM)
    basis = d["avm"].values * fixes["avm_discount"]
    with np.errstate(divide="ignore", invalid="ignore"):
        gy = np.where((np.isnan(rent_used)) | (basis == 0), np.nan, rent_used * 12 / basis)
    d["gross_yield"] = gy

    # ---- AI buy-box match (gate) — note: yield is NOT gated (matches Excel)
    match = ((d["proptype"] == bb["proptype"]) &
             (d["yearbuilt"] >= yb_lo) & (d["yearbuilt"] <= yb_hi) &
             (d["sqft"] >= sf_lo) & (d["sqft"] <= sf_hi) &
             (d["avm"] >= pr_lo) & (d["avm"] <= pr_hi) &
             (d["approved"] == 1)).astype(int)
    d["match"] = match
    m = match == 1

    # ---- sub-scores (0 when not a match)
    yld = np.where(m & ~np.isnan(gy),
                   clamp01((gy - yfloor) / (ytarget - yfloor)) * 100, 0.0)
    prc = np.where(m, clamp01((pr_hi - d["avm"]) / (pr_hi - pr_lo)) * 100, 0.0)
    absc = np.where(m, np.where(d["corp"] == "Y", 100.0, 0.0), 0.0)
    tenure = np.where(d["sale_serial"].isna() | (d["sale_serial"] == 0),
                      np.nan, (asof - d["sale_serial"]) / 365.25)
    d["tenure"] = tenure
    tensc = np.where(~m, 0.0,
            np.where(np.isnan(tenure), 50.0, clamp01(tenure / sat) * 100))
    mid = (sf_lo + sf_hi) / 2; half = (sf_hi - sf_lo) / 2
    sqftfit = np.where(m, np.maximum(0, 1 - np.abs(d["sqft"] - mid) / half) * 100, 0.0)
    yearfit = np.where(m, clamp01((d["yearbuilt"] - yb_lo) / (yb_hi - yb_lo)) * 100, 0.0)

    zarr = d["zip"].values
    proven = np.where(m, np.array([proven_d.get(z, 0) or 0 for z in zarr]), 0.0)
    density = np.where(m, np.array([density_d.get(z, 0) or 0 for z in zarr]), 0.0)
    target = np.where(m, np.array([target_d.get(z, 0) or 0 for z in zarr]), 0.0)
    momentum = np.where(m, np.array([momentum_d.get(z, 0) or 0 for z in zarr]), 0.0)
    risk = np.where(m, np.array([msa2risk.get(zip2msa.get(z), 0) or 0 for z in zarr]), 0.0)

    for nm, arr in [("yield_sc",yld),("price_sc",prc),("abs_sc",absc),("ten_sc",tensc),
                    ("sqft_fit",sqftfit),("year_fit",yearfit),("proven_sc",proven),
                    ("density_sc",density),("target_sc",target),("momentum_sc",momentum),
                    ("risk_sc",risk)]:
        d[nm] = arr

    raw = (yld*W["w_yield"] + prc*W["w_price"] + absc*W["w_absentee"] + tensc*W["w_tenure"]
           + proven*W["w_proven"] + density*W["w_density"] + target*W["w_target"]
           + momentum*W["w_momentum"] + sqftfit*W["w_sqft"] + yearfit*W["w_year"]) / Wtot
    haircut = 1 - np.maximum(0, risk - rbase) / (100 - rbase) * rsens
    total = np.where(m, raw * haircut, 0.0)
    d["total_score"] = total

    tier = np.where(~m, "Not a Match",
            np.where(total >= t1, "Tier 1 - Strong",
            np.where(total >= t2, "Tier 2 - Moderate", "Tier 3 - Watch")))
    d["tier"] = tier
    return d


if __name__ == "__main__":
    DATA = r"C:\Users\AshokReddy\Downloads\International\data"
    refs = load_refs(DATA)
    uni = pd.read_parquet(f"{DATA}/universe_raw.parquet")
    print("universe:", uni.shape)
    scored = score_universe(uni, refs)  # parity mode (no fixes)

    # ---- PARITY vs Excel cached values
    print("\n===== PARITY CHECK (engine vs Excel cached) =====")
    print("match count  : engine=%d  excel=%d" % (scored["match"].sum(), int(uni["x_match"].sum())))
    em = scored["tier"].value_counts().to_dict()
    print("engine tiers :", em)
    xm = uni["x_tier"].value_counts().to_dict()
    print("excel  tiers :", xm)
    # numeric diffs on matched rows
    msk = scored["match"] == 1
    for ecol, xcol in [("gross_yield","x_yield"),("total_score","x_total"),
                       ("tenure","x_tenure"),("max_rent","x_maxrent")]:
        a = pd.to_numeric(scored.loc[msk, ecol], errors="coerce")
        b = pd.to_numeric(uni.loc[msk, xcol], errors="coerce")
        diff = (a - b).abs()
        print(f"{ecol:13s}: max_absdiff={np.nanmax(diff):.6g}  mean_absdiff={np.nanmean(diff):.6g}")
    # tier agreement
    agree = (scored["tier"].values == uni["x_tier"].astype(str).values).mean()
    print(f"tier agreement: {agree*100:.4f}%")
