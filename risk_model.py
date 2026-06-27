"""
risk_model.py — multi-dimension acquisition risk model.

Produces a *decision-grade* risk profile for a single property (or a portfolio of
rows). Design goals from the desk:
  • Surface EVERY material and minor risk — nothing hidden, nothing rounded away.
  • Each flag carries: category, severity, plain-English detail, the evidence that
    triggered it, a concrete mitigation, and an honest source (computed vs. needs a
    paid report). We never fabricate litigation/title/environmental data.
  • Offline & deterministic so it runs the same on a plane or on Railway. FEMA flood
    is pulled live only when lat/lon are present AND the network is reachable.

Output: {grade, score(0-100 risk), band, summary, risks[...], by_category, decision}.
Higher score = riskier. Grade A (safest) … F (avoid / deep diligence).
"""
from __future__ import annotations

SEV_RANK = {"Critical": 5, "High": 4, "Medium": 3, "Low": 2, "Minor": 1, "Info": 0}
SEV_WT   = {"Critical": 100, "High": 74, "Medium": 50, "Low": 28, "Minor": 13, "Info": 0}

# State overlays (publicly documented regimes — used as flags, not as legal advice)
CLIMATE = {
    "FL": "hurricane + storm-surge flooding; statewide insurance crisis",
    "LA": "hurricane + flooding; severe insurance availability crisis",
    "TX": "hail/wind + rising premiums; coastal surge on the Gulf",
    "CA": "wildfire + earthquake; FAIR-plan dependence in many ZIPs",
    "MS": "hurricane + flooding", "AL": "hurricane (coastal counties)",
    "SC": "hurricane + coastal flooding", "NC": "hurricane + coastal flooding",
    "GA": "hurricane (coastal)", "OK": "tornado/hail", "KS": "tornado/hail",
}
RENT_CONTROL = {
    "CA": "statewide rent cap (AB 1482)", "OR": "statewide rent cap (SB 608)",
    "NY": "rent stabilization in many markets", "NJ": "widespread local rent control",
    "MD": "local caps (e.g., Montgomery Co.)", "MN": "local caps (St. Paul)",
    "DC": "rent control", "CO": "local caps emerging",
}
TENANT_FRIENDLY = {"CA", "NY", "NJ", "OR", "WA", "MA", "IL", "DC", "MD", "MN", "CO", "VT", "ME"}
JUDICIAL_FC = {"FL", "NY", "NJ", "IL", "OH", "IN", "SC", "LA", "CT", "DE", "KY",
               "ND", "NM", "PA", "VT", "WI", "KS", "OK", "IA"}  # court-supervised => slower exits


def _f(x, d=0.0):
    try:
        v = float(x)
        return v if v == v else d  # filter NaN
    except (TypeError, ValueError):
        return d


def _fema(lat, lon):
    """Live FEMA NFHL flood-zone lookup via site_intel.flood. Returns (zone, in_sfha, ok)."""
    try:
        import site_intel
        z = site_intel.flood(_f(lat), _f(lon))
        zone = z.get("zone")
        if zone and "failed" not in (z.get("risk") or "") and "unknown" not in (z.get("risk") or ""):
            return zone, bool(z.get("sfha")), True
    except Exception:
        pass
    return None, None, False


def assess(prop: dict, refs=None, rate=0.07, dscr_min=1.20, ltv=0.75, opex_ratio=0.42,
           portfolio_state_share=None, live_flood=True):
    """Assess one property. `prop` uses canonical scored columns."""
    R = []  # risk flags
    def flag(cat, title, sev, detail, evidence, mitigation, source="computed"):
        R.append({"category": cat, "title": title, "severity": sev, "detail": detail,
                  "evidence": evidence, "mitigation": mitigation, "source": source})

    st   = str(prop.get("state", "") or "").upper()[:2]
    avm  = _f(prop.get("avm"))
    rent = _f(prop.get("market_rent"))
    gy   = _f(prop.get("gross_yield"))
    yb   = int(_f(prop.get("yearbuilt")))
    sqft = _f(prop.get("sqft"))
    ten  = _f(prop.get("tenure"))
    corp = str(prop.get("corp", "")).upper() == "Y"
    lat, lon = prop.get("lat"), prop.get("lon")

    # ---- 1. FLOOD (live FEMA, else honest "unverified") ---------------------
    zone, sfha, ok = _fema(lat, lon) if live_flood else (None, None, False)
    if ok and sfha:
        flag("Climate / Flood", f"In FEMA Special Flood Hazard Area (Zone {zone})", "High",
             "Lender-mandated flood insurance; premiums volatile under Risk Rating 2.0 and can swing the pro-forma.",
             f"FEMA NFHL zone {zone}", "Quote flood insurance BEFORE LOI; underwrite to the bound premium, not an estimate.",
             "FEMA NFHL (live)")
    elif ok and zone:
        flag("Climate / Flood", f"Outside SFHA (FEMA Zone {zone})", "Info",
             "Minimal mapped flood risk, but pluvial/flash flooding is not mapped by FEMA.",
             f"FEMA NFHL zone {zone}", "Optional: check First Street / pluvial flood score for completeness.",
             "FEMA NFHL (live)")
    else:
        flag("Climate / Flood", "Flood zone not verified", "Low",
             "Could not confirm FEMA flood zone (offline or no coordinates). Treat as unknown, not as safe.",
             "no FEMA response", "Pull FEMA NFHL / elevation certificate before close.", "needs check")

    # ---- 2. CLIMATE / INSURANCE overlay ------------------------------------
    if st in CLIMATE:
        sev = "High" if st in ("FL", "LA", "CA") else "Medium"
        flag("Climate / Insurance", f"{st}: {CLIMATE[st]}", sev,
             "Insurance cost & availability is a top driver of NOI erosion in this state; carriers are non-renewing.",
             f"state = {st}", "Bind a real insurance quote during diligence; stress NOI for +25–40% premium.",
             "state climate/insurance profile")

    # ---- 3. REGULATORY: rent control / tenant-friendly ----------------------
    if st in RENT_CONTROL:
        flag("Regulatory", f"Rent regulation: {RENT_CONTROL[st]}", "Medium",
             "Caps annual rent growth and can limit your ability to mark to market — directly hits exit value.",
             f"state = {st}", "Underwrite rent growth to the statutory cap; verify local ordinances at the parcel.",
             "public statute")
    if st in TENANT_FRIENDLY:
        flag("Regulatory", f"Tenant-friendly jurisdiction ({st})", "Low",
             "Longer eviction timelines and stricter notice rules raise carry cost on turnover/non-payment.",
             f"state = {st}", "Budget longer vacancy on turnover; use local counsel for the lease.",
             "jurisdiction profile")
    if st in JUDICIAL_FC:
        flag("Liquidity / Exit", f"Judicial-foreclosure state ({st})", "Minor",
             "If you ever need to foreclose/repossess, court supervision adds months — an exit-timing risk.",
             f"state = {st}", "Hold extra reserves; factor slower forced-exit timelines.",
             "jurisdiction profile")

    # ---- 4. AGE: lead paint / deferred capex --------------------------------
    if 0 < yb < 1978:
        flag("Physical / Legal", f"Pre-1978 build ({yb}) — federal lead-paint disclosure", "Medium",
             "Lead-based-paint disclosure is mandatory; remediation can be costly and is a tenant-litigation vector.",
             f"yearbuilt = {yb}", "Order a lead/asbestos screen; reserve for abatement; use EPA RRP-certified contractors.",
             "EPA/HUD rule (computed)")
    if 0 < yb < 1965:
        flag("Physical / Capex", f"Older structure ({yb}) — deferred-maintenance exposure", "Medium",
             "Galvanized/cast-iron plumbing, knob-and-tube wiring, and original systems concentrate near-term capex.",
             f"yearbuilt = {yb}", "Full inspection + sewer scope; reserve $8–15k for systems beyond normal turn.",
             "computed")
    elif 1965 <= yb < 1985:
        flag("Physical / Capex", f"Mid-age structure ({yb}) — systems nearing end of life", "Low",
             "Roof, HVAC and water heater are likely in replacement window within the hold.",
             f"yearbuilt = {yb}", "Verify roof/HVAC age; set a normal capex reserve ($1,200–1,800/yr).", "computed")

    # ---- 5. SIZE / functional fit -------------------------------------------
    if 0 < sqft < 900:
        flag("Asset Fit", f"Small footprint ({sqft:.0f} sf)", "Low",
             "Below ~900 sf narrows the renter and resale pool and caps achievable rent.",
             f"sqft = {sqft:.0f}", "Confirm 2+ functional bedrooms; price the rent to the smaller comp set.", "computed")
    elif sqft > 3200:
        flag("Asset Fit", f"Large footprint ({sqft:.0f} sf)", "Minor",
             "Large SFR rents rarely scale linearly — rent-per-sf and yield compress.",
             f"sqft = {sqft:.0f}", "Underwrite rent to bed/bath, not sf; verify the rent comp set.", "computed")

    # ---- 6. CASH-FLOW: negative leverage / DSCR -----------------------------
    if avm > 0 and rent > 0:
        noi = rent * 12 * (1 - opex_ratio)
        cap = noi / avm if avm else 0
        ann_debt = avm * ltv * (rate + 0.012)  # rate + ~principal proxy
        dscr = (noi / ann_debt) if ann_debt else 0
        if cap < rate:
            flag("Financial / Leverage", f"Negative leverage (cap {cap*100:.1f}% < rate {rate*100:.1f}%)", "High",
                 "Debt costs more than the asset yields — leverage REDUCES cash-on-cash. Returns hinge on appreciation.",
                 f"cap≈{cap*100:.1f}%, rate={rate*100:.1f}%", "Lower LTV, negotiate price, or require a clear rent-bump thesis.",
                 "computed")
        if dscr < dscr_min:
            sev = "High" if dscr < 1.05 else "Medium"
            flag("Financial / DSCR", f"Thin debt coverage (DSCR≈{dscr:.2f})", sev,
                 f"Below the {dscr_min:.2f} lenders want; little cushion for vacancy or expense shocks.",
                 f"DSCR≈{dscr:.2f}", "Cut leverage to reach ≥1.25x; build 3–6 mo. of debt-service reserve.", "computed")
    if 0 < gy < 0.08:
        flag("Financial / Yield", f"Thin gross yield ({gy*100:.1f}%)", "Medium",
             "Sub-8% gross rarely nets positive cash flow after taxes, insurance, capex and management.",
             f"gross yield = {gy*100:.1f}%", "Require a value-add/rent-bump path or a discount to make the math work.",
             "computed")

    # ---- 7. VALUATION band --------------------------------------------------
    if 0 < avm < 80000:
        flag("Valuation / Class", f"Low basis (${avm:,.0f})", "Medium",
             "Sub-$80k often signals C/D-class location: lender minimums, insurance haircuts, thin buyer pool at exit.",
             f"AVM = ${avm:,.0f}", "Verify neighborhood class, lendability and exit liquidity before committing.", "computed")
    elif avm > 550000:
        flag("Valuation / Exit", f"High basis (${avm:,.0f})", "Low",
             "Higher-price SFR yields compress and the rental-buyer pool thins at resale.",
             f"AVM = ${avm:,.0f}", "Confirm rent supports the basis; plan an owner-occupant exit, not just investor.", "computed")
    # AVM is a model — always a confidence flag
    flag("Valuation / Confidence", "Price is an AVM, not an appraisal", "Minor",
         "All pricing here is an automated valuation; true value needs comps/BPO/appraisal.",
         "AVM source", "Order a BPO or appraisal before hard money; re-trade if comps disagree.", "computed")

    # ---- 8. TENURE / turnover ----------------------------------------------
    if 0 < ten < 2:
        flag("Operational / Turnover", f"Short owner tenure ({ten:.1f} yr)", "Low",
             "Recent purchase/flip can mean fresh-but-shallow rehab or a thin equity owner — verify work quality.",
             f"tenure = {ten:.1f} yr", "Inspect rehab quality; confirm no flip-and-flip permit gaps.", "computed")
    elif ten > 25:
        flag("Operational / Condition", f"Very long tenure ({ten:.0f} yr)", "Low",
             "Decades-long ownership often means dated systems/finishes and possible probate/estate complexity.",
             f"tenure = {ten:.0f} yr", "Heavy inspection; confirm clear title/heirs; reserve for full modernization.", "computed")

    # ---- 9. OWNERSHIP / entity ---------------------------------------------
    if corp:
        flag("Ownership / Legal", "Corporate / entity owner", "Minor",
             "Entity-owned sales add diligence (authority, liens, entity status) and intersect large-investor scrutiny.",
             "owner = corporate", "Confirm entity good-standing & signing authority; standard lien/UCC search.", "computed")

    # ---- 10. Diligence items we will NOT fabricate ---------------------------
    for cat, title, detail, mit in [
        ("Title / Liens", "Title & lien status unverified",
         "Open mortgages, tax liens, mechanic's liens, easements and code violations are not in this dataset.",
         "Order a title commitment / O&E report before LOI."),
        ("Environmental", "Environmental (Phase I) unverified",
         "Proximity to USTs, industrial sites, or contamination is not screened here.",
         "Order a Phase I ESA / EDR radius report if any industrial proximity."),
        ("Litigation", "Litigation / code-enforcement unverified",
         "Active suits, HOA disputes, or open permits/violations are not in this dataset.",
         "Run a court-records / code-enforcement search in diligence."),
    ]:
        flag(cat, title, "Low", detail, "not in dataset", mit, "needs paid report")

    # ---- 11. Single-asset concentration / portfolio share -------------------
    flag("Portfolio / Concentration", "Single-asset idiosyncratic risk", "Info",
         "One SFR = binary occupancy (0% or 100%). Vacancy/repair shocks aren't diversified.",
         "asset type = SFR", "Diversify across ≥5–10 doors and ≥2 submarkets to smooth cash flow.", "computed")
    if portfolio_state_share and portfolio_state_share.get(st, 0) > 0.5:
        flag("Portfolio / Concentration", f"State concentration in {st} ({portfolio_state_share[st]*100:.0f}%)", "Medium",
             "Over half the portfolio in one state concentrates climate, regulatory and economic risk.",
             f"{st} share = {portfolio_state_share[st]*100:.0f}%", "Cap any single state at ~40%; diversify metros.", "computed")

    return _score(R, prop)


def _score(R, prop):
    """Blend flags into an overall risk score (0–100, higher = riskier) + grade."""
    # weighted by severity; diminishing returns so many minor flags don't dominate
    pts = sorted((SEV_WT[r["severity"]] for r in R), reverse=True)
    acc, decay = 0.0, 1.0
    for p in pts:
        acc += p * decay
        decay *= 0.55  # each further flag counts less
    score = round(min(100, acc / 1.8), 1)
    crit = sum(1 for r in R if r["severity"] == "Critical")
    high = sum(1 for r in R if r["severity"] == "High")
    # a single High/Critical sets a floor
    if crit: score = max(score, 80)
    elif high: score = max(score, max(55, score))
    grade = ("A" if score < 20 else "B" if score < 38 else "C" if score < 55 else
             "D" if score < 72 else "F")
    band = {"A": "Low risk", "B": "Modest risk", "C": "Moderate risk",
            "D": "Elevated risk", "F": "High risk"}[grade]
    order = sorted(R, key=lambda r: (-SEV_RANK[r["severity"]], r["category"]))
    by_cat = {}
    for r in R:
        by_cat.setdefault(r["category"], []).append(r["title"])
    counts = {s: sum(1 for r in R if r["severity"] == s) for s in SEV_RANK}
    top = [r for r in order if r["severity"] in ("Critical", "High", "Medium")][:5]
    decision = (
        "Avoid or require deep diligence + repricing." if grade == "F" else
        "Proceed only with mitigations priced in and reserves set." if grade == "D" else
        "Acceptable with standard diligence; address the flagged items." if grade == "C" else
        "Clean profile — standard diligence; monitor the minor flags." if grade == "B" else
        "Low-risk profile — proceed with normal diligence.")
    summary = (f"{band}: {counts['Critical']} critical, {counts['High']} high, "
               f"{counts['Medium']} medium, {counts['Low']} low, {counts['Minor']} minor flags.")
    return {"grade": grade, "score": score, "band": band, "summary": summary,
            "decision": decision, "counts": counts, "risks": order,
            "by_category": by_cat, "top": top}


def assess_portfolio(rows, refs=None, **kw):
    """Aggregate risk across many rows: per-state share + average grade + worst flags."""
    rows = [r for r in rows if r]
    n = len(rows) or 1
    share = {}
    for r in rows:
        st = str(r.get("state", "")).upper()[:2]
        share[st] = share.get(st, 0) + 1
    share = {k: v / n for k, v in share.items()}
    results = [assess(r, refs, portfolio_state_share=share, **kw) for r in rows]
    avg = round(sum(x["score"] for x in results) / n, 1)
    grade = ("A" if avg < 20 else "B" if avg < 38 else "C" if avg < 55 else "D" if avg < 72 else "F")
    worst = sorted(results, key=lambda x: -x["score"])[:5]
    return {"n": n, "avg_score": avg, "grade": grade, "state_share": share,
            "worst": [{"score": w["score"], "grade": w["grade"], "summary": w["summary"]} for w in worst]}


if __name__ == "__main__":
    demo = {"state": "FL", "avm": 92000, "market_rent": 1150, "gross_yield": 0.15,
            "yearbuilt": 1958, "sqft": 980, "tenure": 1.2, "corp": "Y", "lat": 27.9, "lon": -82.4}
    out = assess(demo)
    print("grade", out["grade"], "score", out["score"], "-", out["summary"])
    for r in out["risks"]:
        print(f"  [{r['severity']:>8}] {r['category']}: {r['title']}  ({r['source']})")
