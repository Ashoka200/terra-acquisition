"""
calc_trace.py — "show your work" for a single property.

Reproduces the EXACT engine math (re_core gate + metric normalization + risk haircut
+ tier, and re_underwrite's DCF) one property at a time, emitting an ordered, human-
readable trace: every step carries the formula (with the real numbers substituted),
the value, and a pass/fail where relevant. The recomputed score is checked against the
stored total_score so the trace is provably the same model that ranked the universe.

This single trace drives BOTH the in-app Calculations tab and the live-formula Excel
export, so what you read on screen is exactly what the spreadsheet recomputes.
"""
import numpy as np, pandas as pd
import re_core, re_underwrite as U


def _z(zp):
    return str(zp).replace(".0", "").strip()

def _f(x, d=np.nan):
    try:
        v = float(x); return v if v == v else d
    except (TypeError, ValueError):
        return d

def _loc_values(zp, refs):
    """Reproduce prepare_sfr's zip→(proven/density/target/momentum) + zip→msa→risk lookups."""
    zp = _z(zp); rr = refs["rent"]
    zip2msa = rr.dropna(subset=["msa"]).drop_duplicates("zip").set_index("zip")["msa"].to_dict()
    msa2risk = refs["risk"].dropna(subset=["risk"]).set_index("msa")["risk"].to_dict()
    zs = refs["zip_stats"].drop_duplicates("zip").set_index("zip")
    out = {}
    for src, col in [("proven", "proven_v"), ("density", "density_v"),
                     ("target", "target_v"), ("momentum", "momentum_v")]:
        out[col] = (zs[src].to_dict().get(zp, 0) or 0) if src in zs.columns else 0
    out["risk"] = msa2risk.get(zip2msa.get(zp), 0) or 0
    out["approved"] = zp in set(_z(z) for z in rr["zip"].dropna())
    out["msa"] = zip2msa.get(zp, "—")
    return out

def _scalar_norm(kind, value, p, match=True):
    arr = np.array([value], dtype=object) if kind == "binary" else np.array([_f(value)], dtype=float)
    return float(re_core._norm(kind, arr, p, np.array([match]))[0])

def _pctnum(x):  # 0.092 -> "9.2%"
    return f"{x*100:.1f}%" if x is not None and x == x else "—"


# ----------------------------------------------------------- GATE
def _gate_trace(prop, profile, loc):
    rows, passed = [], True
    label = {"proptype": "Property type", "yearbuilt": "Year built", "sqft": "Living area (sqft)",
             "avm": "AVM (price proxy)", "approved": "Rent-benchmarked zip"}
    for c in profile["gate"]:
        fld = c["field"]; op = c["op"]
        if fld == "approved":
            ok = bool(loc["approved"]); val = "yes" if ok else "no"
            formula = f'zip {_z(prop.get("zip"))} ∈ approved rent list  →  {val}'
        elif fld == "proptype":
            pv = prop.get("proptype", "SFR"); ok = (pv == c["value"]) or (pv in (None, "", "nan"))
            formula = f'"{pv or "SFR"}" = "{c["value"]}"'
            val = pv or "SFR"
        else:
            v = _f(prop.get(fld))
            if op == "between":
                ok = (v >= c["lo"]) and (v <= c["hi"])
                formula = f'{c["lo"]:,.0f} ≤ {v:,.0f} ≤ {c["hi"]:,.0f}'
            elif op in ("ge", "le"):
                ok = (v >= c["value"]) if op == "ge" else (v <= c["value"])
                formula = f'{v:,.0f} {"≥" if op=="ge" else "≤"} {c["value"]:,.0f}'
            else:
                ok = True; formula = str(v)
            val = f'{v:,.0f}'
        passed = passed and ok
        rows.append({"label": label.get(fld, fld), "field": fld, "formula": formula,
                     "value": val, "pass": bool(ok)})
    return rows, passed


# ----------------------------------------------------------- SCORE
def _norm_formula(kind, p, inp_label, val):
    v = _f(val)
    if kind == "band":
        return (f'clamp(({_disp(v)} − {_disp(p["lo"])}) / ({_disp(p["hi"])} − {_disp(p["lo"])})) × 100',
                "higher is better")
    if kind == "band_inv":
        return (f'clamp(({_disp(p["hi"])} − {_disp(v)}) / ({_disp(p["hi"])} − {_disp(p["lo"])})) × 100',
                "lower is better")
    if kind == "triangular":
        return (f'max(0, 1 − |{_disp(v)} − {_disp(p["center"])}| / {_disp(p["half"])}) × 100',
                "closeness to center")
    if kind == "ratio_cap":
        return (f'min(1, {_disp(v)} / {_disp(p["cap"])}) × 100', "saturating ratio")
    if kind == "binary":
        return (f'IF({inp_label} = "{p["value"]}", 100, 0)', "flag")
    if kind == "passthrough":
        return (f'{_disp(v)}  (precomputed market index, already 0–100)', "lookup")
    return ("—", "")

def _disp(x):
    if x is None or (isinstance(x, float) and x != x): return "—"
    ax = abs(x)
    if 0 < ax < 1: return f'{x:.3g}'
    return f'{x:,.0f}' if ax >= 100 else f'{x:,.2f}'

def _score_trace(prop, profile, loc, matched):
    val = {"gross_yield": _f(prop.get("gross_yield")), "avm": _f(prop.get("avm")),
           "corp": prop.get("corp", "N"), "tenure": _f(prop.get("tenure")),
           "sqft": _f(prop.get("sqft")), "yearbuilt": _f(prop.get("yearbuilt")),
           "proven_v": loc["proven_v"], "density_v": loc["density_v"],
           "target_v": loc["target_v"], "momentum_v": loc["momentum_v"]}
    metrics = [m for m in profile["metrics"]
               if m.get("weight", 0) and m.get("on", True) and m.get("input") in val]
    Wtot = sum(m["weight"] for m in metrics) or 1
    rows, acc = [], 0.0
    for m in metrics:
        raw_in = val[m["input"]]
        sub = _scalar_norm(m["norm"]["kind"], raw_in, m["norm"], matched)
        contrib = sub * m["weight"]
        acc += contrib
        formula, hint = _norm_formula(m["norm"]["kind"], m["norm"], m["label"], raw_in)
        disp_in = (f'{raw_in*100:.1f}%' if m["input"] == "gross_yield" else
                   (str(raw_in) if m["norm"]["kind"] == "binary" else _disp(raw_in)))
        rows.append({"pillar": m["pillar"], "metric": m["label"], "input": disp_in, "hint": hint,
                     "formula": formula, "subscore": round(sub, 1), "weight": m["weight"],
                     "contribution": round(contrib, 1)})
    raw = acc / Wtot
    r = profile["risk"]; risk = loc["risk"] if matched else 0.0
    haircut = 1 - max(0, risk - r["baseline"]) / (100 - r["baseline"]) * r["sensitivity"]
    total = raw * haircut if matched else 0.0
    t1, t2 = profile["tiers"]["tier1"], profile["tiers"]["tier2"]
    tier = ("Not a Match" if not matched else "Tier 1 - Strong" if total >= t1
            else "Tier 2 - Moderate" if total >= t2 else "Tier 3 - Watch")
    summary = {
        "weight_total": round(Wtot, 1), "weighted_sum": round(acc, 1),
        "raw_score": round(raw, 2),
        "raw_formula": f"Σ(sub-score × weight) / Σ(weight)  =  {acc:,.0f} / {Wtot:,.0f}",
        "risk_index": round(risk, 1), "risk_baseline": r["baseline"], "risk_sensitivity": r["sensitivity"],
        "haircut": round(haircut, 4),
        "haircut_formula": f'1 − max(0, {risk:.0f} − {r["baseline"]:.0f}) / (100 − {r["baseline"]:.0f}) × {r["sensitivity"]:.2f}',
        "total": round(total, 2),
        "total_formula": f"{raw:.2f} × {haircut:.3f}",
        "tier1_cut": t1, "tier2_cut": t2, "tier": tier,
        "tier_formula": f'IF(total ≥ {t1:.0f} → Tier 1; ≥ {t2:.0f} → Tier 2; else Tier 3)'}
    return rows, summary


# ----------------------------------------------------------- UNDERWRITING
def _uw_trace(price, rent, a):
    uw = U.underwrite(price, rent, a)
    gsr = rent * 12; egi = gsr * (1 - a["vacancy"])
    steps = [
        ("All-in basis", f'price × (1 + closing {_pctnum(a["closing"])}) + rehab {_money(a["rehab"])}', uw["all_in"], "money"),
        ("Senior loan", f'price {_money(price)} × LTV {_pctnum(a["ltv"])}', uw["loan"], "money"),
        ("Cash invested", f'all-in − loan + loan × points {_pctnum(a["points"])}', uw["cash_invested"], "money"),
        ("Gross scheduled rent", f'rent {_money(rent)} × 12', gsr, "money"),
        ("Effective gross income", f'GSR × (1 − vacancy {_pctnum(a["vacancy"])})', egi, "money"),
        ("Operating expenses", f'EGI×PM {_pctnum(a["pm"])} + GSR×maint {_pctnum(a["maint"])} + price×tax {_pctnum(a["tax"])} + ins {_money(a["ins"])} + hoa + other {_money(a["other"])}', egi - uw["noi"], "money"),
        ("Net operating income", "EGI − operating expenses", uw["noi"], "money"),
        ("Annual debt service", f'−PMT(rate {_pctnum(a["rate"])}/12, amort {int(a["amort"])}×12, loan) × 12', uw["debt_service"], "money"),
        ("Cash flow before tax", "NOI − debt service", uw["cash_flow"], "money"),
        ("Going-in cap rate", "NOI / all-in basis", uw["cap_rate"], "pct2"),
        ("Cash-on-cash", "cash flow / cash invested", uw["coc"], "pct2"),
        ("DSCR", "NOI / annual debt service", uw["dscr"], "num2"),
    ]
    return [{"label": l, "formula": fm, "value": v, "fmt": f} for l, fm, v, f in steps], uw

def _money(x):
    try: return f"${x:,.0f}"
    except Exception: return "—"


# ----------------------------------------------------------- entry point
def trace(prop, refs, profile, assumptions, uw_discount=0.9):
    """Full calculation trace for one property dict (canonical scored columns)."""
    loc = _loc_values(prop.get("zip"), refs)
    gate_rows, matched = _gate_trace(prop, profile, loc)
    score_rows, summ = _score_trace(prop, profile, loc, matched)
    price = round(_f(prop.get("avm")) * uw_discount)
    rent = _f(prop.get("market_rent"))
    uw_rows, uw = _uw_trace(price, rent, assumptions) if (rent == rent and price) else ([], {})
    stored = _f(prop.get("total_score"))
    recomputed = summ["total"]
    ties = abs(recomputed - stored) < 0.6 if stored == stored else None
    return {
        "property": {k: prop.get(k) for k in ("apn", "address", "city", "state", "zip",
                     "avm", "market_rent", "gross_yield", "sqft", "yearbuilt", "beds",
                     "tenure", "corp", "total_score", "tier")},
        "msa": loc["msa"],
        "gate": {"rows": gate_rows, "passed": matched,
                 "verdict": "PASS — enters scoring" if matched else "FAIL — scored 0 (Not a Match)"},
        "score": {"rows": score_rows, **summ},
        "underwrite": {"rows": uw_rows, "price": price, "rent": rent, "discount": uw_discount},
        "verify": {"recomputed": recomputed, "stored": round(stored, 2) if stored == stored else None,
                   "ties": ties},
        "assumptions": assumptions,
    }


if __name__ == "__main__":
    import app
    p = app.t_lookup_property(query="129A01033000")
    prof = __import__("projects").get_project(app.ACTIVE_PID)["profile"]
    t = trace(p, app.REFS, prof, app.ASSUMP)
    print("GATE:", t["gate"]["verdict"])
    for g in t["gate"]["rows"]:
        print(f'  [{"PASS" if g["pass"] else "FAIL"}] {g["label"]}: {g["formula"]}')
    print("\nSCORE rows:")
    for s in t["score"]["rows"]:
        print(f'  {s["pillar"]:>10} · {s["metric"]:<22} {s["formula"]}  = {s["subscore"]} × {s["weight"]} = {s["contribution"]}')
    print("\n  raw:", t["score"]["raw_formula"], "=", t["score"]["raw_score"])
    print("  haircut:", t["score"]["haircut_formula"], "=", t["score"]["haircut"])
    print("  total:", t["score"]["total_formula"], "=", t["score"]["total"], "→", t["score"]["tier"])
    print("  VERIFY recomputed", t["verify"]["recomputed"], "vs stored", t["verify"]["stored"], "ties:", t["verify"]["ties"])
    print("\nUNDERWRITE:")
    for u in t["underwrite"]["rows"]:
        print(f'  {u["label"]:<24} {u["formula"]}')
