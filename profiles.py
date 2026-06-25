"""
profiles.py — property-type knowledge library. Picking a type auto-loads its
buy box (gate), scoring metrics + weights, assumptions, and the right underwriting
module. SFR is data-proven (re_core); the others are institutional best-practice
defaults, ready for their data.

Each profile is editable config (a user can reweight, toggle, retune, or add metrics).
Underwriting differs by type — that math is honest per type, not one-size-fits-all.
"""
import numpy_financial as npf
import re_core

# ----------------------------------------------------------- static profiles
# (SFR is built from data via re_core.build_sfr_profile; defined here for others.)
PROFILE_DEFS = {
    "MF": {
        "key": "mf", "label": "Multifamily (2–50 units)", "property_type": "MF",
        "gate": [
            {"field": "proptype", "op": "in", "value": ["MF", "Duplex", "Triplex", "Fourplex", "Apartment"]},
            {"field": "units", "op": "between", "lo": 2, "hi": 50},
            {"field": "price", "op": "between", "lo": 250000, "hi": 6000000},
            {"field": "cap_rate", "op": "ge", "value": 0.055},
        ],
        "metrics": [
            {"key": "cap", "label": "Going-in Cap Rate", "pillar": "Return", "weight": 28, "input": "cap_rate",
             "norm": {"kind": "band", "lo": 0.05, "hi": 0.09}},
            {"key": "grm", "label": "GRM (low = better)", "pillar": "Return", "weight": 12, "input": "grm",
             "norm": {"kind": "band_inv", "lo": 6, "hi": 14}},
            {"key": "ppu", "label": "Price / Unit", "pillar": "Return", "weight": 12, "input": "price_per_unit",
             "norm": {"kind": "band_inv", "lo": 60000, "hi": 220000}},
            {"key": "ltl", "label": "Loss-to-Lease (upside)", "pillar": "Return", "weight": 12, "input": "loss_to_lease",
             "norm": {"kind": "band", "lo": 0.0, "hi": 0.15}},
            {"key": "scale", "label": "Unit Scale (efficiency)", "pillar": "Location", "weight": 10, "input": "units",
             "norm": {"kind": "band", "lo": 4, "hi": 30}},
            {"key": "occ", "label": "Occupancy", "pillar": "Quality", "weight": 10, "input": "occupancy",
             "norm": {"kind": "band", "lo": 0.80, "hi": 0.97}},
            {"key": "growth", "label": "Submarket Rent Growth", "pillar": "Location", "weight": 8, "input": "rent_growth_mkt",
             "norm": {"kind": "band", "lo": 0.0, "hi": 0.06}},
            {"key": "vintage", "label": "Vintage Fit", "pillar": "Fit", "weight": 8, "input": "yearbuilt",
             "norm": {"kind": "band", "lo": 1975, "hi": 2015}},
        ],
        "risk": {"sensitivity": 0.30, "baseline": 40, "source": "market_risk"},
        "tiers": {"tier1": 70, "tier2": 55}, "underwriter": "mf_noi",
        "assumptions": {"vacancy": 0.07, "opex_ratio": 0.45, "exit_cap": 0.065, "ltv": 0.70,
                        "rate": 0.0675, "amort": 30, "rent_growth": 0.03, "hold": 5, "selling": 0.02},
    },
    "FLIP": {
        "key": "flip", "label": "Fix & Flip", "property_type": "FLIP",
        "gate": [
            {"field": "arv", "op": "between", "lo": 120000, "hi": 600000},
            {"field": "sqft", "op": "between", "lo": 800, "hi": 3500},
            {"field": "spread_pct", "op": "ge", "value": 0.18},
        ],
        "metrics": [
            {"key": "spread", "label": "Equity Spread to ARV", "pillar": "Return", "weight": 38, "input": "spread_pct",
             "norm": {"kind": "band", "lo": 0.15, "hi": 0.40}},
            {"key": "rehab", "label": "Rehab Intensity (low=better)", "pillar": "Return", "weight": 16, "input": "rehab_to_arv",
             "norm": {"kind": "band_inv", "lo": 0.05, "hi": 0.35}},
            {"key": "dom", "label": "Market Velocity (low DOM)", "pillar": "Location", "weight": 16, "input": "days_on_market",
             "norm": {"kind": "band_inv", "lo": 15, "hi": 90}},
            {"key": "ppsf", "label": "Price/Sqft Discount", "pillar": "Return", "weight": 12, "input": "ppsf_discount",
             "norm": {"kind": "band", "lo": 0.0, "hi": 0.30}},
            {"key": "trend", "label": "Neighborhood Trend", "pillar": "Location", "weight": 10, "input": "hpi_trend",
             "norm": {"kind": "band", "lo": -0.02, "hi": 0.08}},
            {"key": "agefit", "label": "Age Fit", "pillar": "Fit", "weight": 8, "input": "yearbuilt",
             "norm": {"kind": "band", "lo": 1950, "hi": 2010}},
        ],
        "risk": {"sensitivity": 0.25, "baseline": 45, "source": "market_risk"},
        "tiers": {"tier1": 70, "tier2": 55}, "underwriter": "flip_arv",
        "assumptions": {"arv_factor": 0.70, "hold_months": 6, "selling": 0.07, "finance_rate": 0.11,
                        "finance_pts": 0.02, "carry_monthly_pct": 0.012},
    },
    "LAND": {
        "key": "land", "label": "Land / Development", "property_type": "LAND",
        "gate": [
            {"field": "acres", "op": "between", "lo": 0.1, "hi": 200},
            {"field": "price_per_acre", "op": "between", "lo": 1000, "hi": 2000000},
            {"field": "buildable", "op": "ge", "value": 1},
        ],
        "metrics": [
            {"key": "ppa", "label": "Price / Acre (low=better)", "pillar": "Return", "weight": 30, "input": "price_per_acre",
             "norm": {"kind": "band_inv", "lo": 5000, "hi": 400000}},
            {"key": "resid", "label": "Residual-Value Margin", "pillar": "Return", "weight": 22, "input": "residual_margin",
             "norm": {"kind": "band", "lo": 0.0, "hi": 0.40}},
            {"key": "growth", "label": "Path of Growth", "pillar": "Location", "weight": 16, "input": "growth_index",
             "norm": {"kind": "band", "lo": 0, "hi": 100}},
            {"key": "util", "label": "Utilities / Buildability", "pillar": "Quality", "weight": 16, "input": "buildability",
             "norm": {"kind": "band", "lo": 0, "hi": 100}},
            {"key": "entitle", "label": "Entitlement Status", "pillar": "Quality", "weight": 16, "input": "entitlement",
             "norm": {"kind": "band", "lo": 0, "hi": 100}},
        ],
        "risk": {"sensitivity": 0.35, "baseline": 40, "source": "market_risk"},
        "tiers": {"tier1": 70, "tier2": 55}, "underwriter": "land_residual",
        "assumptions": {"build_cost_per_unit": 180000, "sale_per_unit": 320000, "units_per_acre": 4,
                        "absorption_years": 3, "discount_rate": 0.15, "soft_cost_pct": 0.20},
    },
}

# ----------------------------------------------------------- underwriters
def uw_sfr_rental(price, monthly_rent, a):
    import re_underwrite
    return re_underwrite.underwrite(price, monthly_rent, a)

def uw_mf_noi(price, units, rent_per_unit, a):
    gsr = units * rent_per_unit * 12
    egi = gsr * (1 - a["vacancy"])
    noi = egi * (1 - a["opex_ratio"])
    value = noi / a["exit_cap"]
    loan = price * a["ltv"]
    ds = -npf.pmt(a["rate"] / 12, a["amort"] * 12, loan) * 12
    return {"gsr": gsr, "egi": egi, "noi": noi, "stabilized_value": value,
            "going_in_cap": noi / price, "dscr": noi / ds if ds else None,
            "price_per_unit": price / units, "grm": price / gsr if gsr else None,
            "debt_service": ds, "cash_flow": noi - ds, "value_vs_price": value / price - 1}

def uw_flip_arv(arv, rehab, a, purchase=None):
    mao = arv * a["arv_factor"] - rehab          # 70% rule max allowable offer
    if purchase is None: purchase = mao
    carry = purchase * a["carry_monthly_pct"] * a["hold_months"]
    selling = arv * a["selling"]
    fin = purchase * a["finance_pts"]
    profit = arv - purchase - rehab - carry - selling - fin
    cash_in = purchase + rehab + carry + fin
    return {"mao_70": mao, "purchase": purchase, "all_in": purchase + rehab + carry + fin,
            "gross_profit": profit, "roi": profit / cash_in if cash_in else None,
            "annualized_roi": (profit / cash_in) * (12 / a["hold_months"]) if cash_in else None,
            "selling_costs": selling, "carry": carry}

def uw_land_residual(acres, price, a):
    units = acres * a["units_per_acre"]
    gross = units * a["sale_per_unit"]
    cost = units * a["build_cost_per_unit"] * (1 + a["soft_cost_pct"])
    residual = (gross - cost) / (1 + a["discount_rate"]) ** a["absorption_years"]
    return {"buildable_units": units, "gross_sellout": gross, "build_cost": cost,
            "residual_land_value": residual, "price": price, "price_per_acre": price / acres if acres else None,
            "residual_margin": residual / price - 1 if price else None,
            "go_no_go": "GO" if residual > price else "NO-GO"}

UNDERWRITERS = {"sfr_rental": uw_sfr_rental, "mf_noi": uw_mf_noi,
                "flip_arv": uw_flip_arv, "land_residual": uw_land_residual}

def get_profile(ptype, refs=None, fixes=None):
    if ptype == "SFR":
        return re_core.build_sfr_profile(refs, fixes or {"avm_discount": 0.90, "rent_realization": 0.95})
    if ptype in PROFILE_DEFS:
        import copy; return copy.deepcopy(PROFILE_DEFS[ptype])
    raise ValueError("unknown property type: " + ptype)

def list_types():
    return [{"type": "SFR", "label": "Single-Family Rental", "status": "data-proven"}] + \
           [{"type": k, "label": v["label"], "status": "ready (awaits data)"} for k, v in PROFILE_DEFS.items()]


if __name__ == "__main__":
    print("=== underwriting self-tests ===")
    mf = uw_mf_noi(2_000_000, units=20, rent_per_unit=1300, a=PROFILE_DEFS["MF"]["assumptions"])
    print("MF: cap=%.3f dscr=%.2f $/unit=%.0f value/price=%.2f" % (mf["going_in_cap"], mf["dscr"], mf["price_per_unit"], mf["value_vs_price"]))
    fl = uw_flip_arv(arv=300000, rehab=45000, a=PROFILE_DEFS["FLIP"]["assumptions"])
    print("FLIP: MAO=%.0f profit=%.0f ROI=%.1f%% annualized=%.1f%%" % (fl["mao_70"], fl["gross_profit"], fl["roi"]*100, fl["annualized_roi"]*100))
    ld = uw_land_residual(acres=10, price=600000, a=PROFILE_DEFS["LAND"]["assumptions"])
    print("LAND: units=%.0f residual=%.0f margin=%.1f%% -> %s" % (ld["buildable_units"], ld["residual_land_value"], ld["residual_margin"]*100, ld["go_no_go"]))
    print("\ntypes:", [t["type"] for t in list_types()])
