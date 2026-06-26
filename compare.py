"""
compare.py — before/after diff between two scored universes.

When a user uploads more data and the model re-scores, this quantifies what changed
so they can trust the update: tier counts, tier-migration flows, the biggest score
movers (on properties present in both), and the make-up of newly added rows.
Pure functions over canonical scored DataFrames.
"""
import pandas as pd

TIERS = ["Tier 1 - Strong", "Tier 2 - Moderate", "Tier 3 - Watch", "Not a Match"]


def _avg(df, c):
    if c not in df.columns or not len(df): return None
    s = pd.to_numeric(df[c], errors="coerce").dropna()
    return round(float(s.mean()), 4) if len(s) else None

def snapshot(df):
    vc = df["tier"].value_counts().to_dict()
    t1 = df[df["tier"] == "Tier 1 - Strong"]
    return {"rows": int(len(df)),
            "tiers": {t: int(vc.get(t, 0)) for t in TIERS},
            "match_rate": round(float((df["tier"] != "Not a Match").mean()), 4) if len(df) else 0,
            "tier1_avg_score": _avg(t1, "total_score"),
            "tier1_avg_yield": _avg(t1, "gross_yield"),
            "tier1_avg_avm": _avg(t1, "avm")}

def compare(old, new):
    b, a = snapshot(old), snapshot(new)
    deltas = {"rows": a["rows"] - b["rows"],
              "tiers": {t: a["tiers"][t] - b["tiers"][t] for t in TIERS},
              "match_rate": round(a["match_rate"] - b["match_rate"], 4)}
    o = old.copy(); n = new.copy()
    o.index = o["apn"].astype(str); n.index = n["apn"].astype(str)
    o = o[~o.index.duplicated()]; n = n[~n.index.duplicated()]
    common = o.index.intersection(n.index)
    added = n.index.difference(o.index)
    dropped = o.index.difference(n.index)
    migration = {}
    movers = {"up": [], "down": []}
    if len(common):
        oc, nc = o.loc[common, "tier"], n.loc[common, "tier"]
        cross = pd.crosstab(oc, nc)
        for ot in cross.index:
            for nt in cross.columns:
                v = int(cross.loc[ot, nt])
                if v and ot != nt:
                    migration[f"{ot.split(' - ')[0]} → {nt.split(' - ')[0]}"] = v
        os_ = pd.to_numeric(o.loc[common, "total_score"], errors="coerce")
        ns_ = pd.to_numeric(n.loc[common, "total_score"], errors="coerce")
        d = (ns_ - os_).dropna()
        def _row(idx, dv):
            r = n.loc[idx]
            return {"apn": idx, "address": str(r.get("address", "")),
                    "old": round(float(os_[idx]), 1), "new": round(float(ns_[idx]), 1),
                    "delta": round(float(dv), 1),
                    "old_tier": str(o.loc[idx, "tier"]), "new_tier": str(r.get("tier", ""))}
        up = d[d > 0.05].sort_values(ascending=False).head(6)
        dn = d[d < -0.05].sort_values().head(6)
        movers = {"up": [_row(i, v) for i, v in up.items()],
                  "down": [_row(i, v) for i, v in dn.items()]}
    add_tiers = n.loc[added, "tier"].value_counts().to_dict() if len(added) else {}
    return {"before": b, "after": a, "deltas": deltas, "migration": migration,
            "movers": movers, "common": int(len(common)),
            "added": {"count": int(len(added)), "tiers": {k: int(v) for k, v in add_tiers.items()}},
            "dropped": int(len(dropped))}
