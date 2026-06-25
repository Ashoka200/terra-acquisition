"""
re_underwrite.py — single-property underwriting, reverse (goal-seek) solver, and
the portfolio DCF (port of the SFR Model 'fIRR' LAMBDA). Pure functions, used by
both the workbook-parity checks and the agent's tool layer.
"""
import numpy as np
import numpy_financial as npf  # PMT/IRR

# ----------------------------------------------------------- single property
def underwrite(price, monthly_rent, a):
    """a = dict of assumptions (rates as decimals)."""
    allin = price*(1+a["closing"]) + a["rehab"]
    loan  = price*a["ltv"]
    cash  = allin - loan + loan*a["points"]
    gsr   = monthly_rent*12
    egi   = gsr*(1-a["vacancy"])
    opex  = egi*a["pm"] + gsr*a["maint"] + price*a["tax"] + a["ins"] + a["hoa"] + a["other"]
    noi   = egi - opex
    ads   = -npf.pmt(a["rate"]/12, a["amort"]*12, loan)*12
    cfbt  = noi - ads
    return {
        "all_in": allin, "loan": loan, "cash_invested": cash, "noi": noi,
        "debt_service": ads, "cash_flow": cfbt,
        "cap_rate": noi/allin, "coc": cfbt/cash, "dscr": noi/ads,
        "gross_yield": gsr/allin, "monthly_cf": cfbt/12,
    }

def reverse_price(target_metric, target, a, monthly_rent):
    """Closed-form purchase price that hits a target Cap / CoC / DSCR."""
    gsr = monthly_rent*12
    egi = gsr*(1-a["vacancy"])
    fixed_opex = egi*a["pm"] + gsr*a["maint"] + a["ins"] + a["hoa"] + a["other"]
    k = -npf.pmt(a["rate"]/12, a["amort"]*12, 1)*12   # annual DS per $1 loan
    t = a["tax"]
    if target_metric == "cap":
        P = (egi - fixed_opex - target*a["rehab"]) / (target*(1+a["closing"]) + t)
    elif target_metric == "dscr":
        P = (egi - fixed_opex) / (t + target*k*a["ltv"])
    elif target_metric == "coc":
        P = (egi - fixed_opex - target*a["rehab"]) / (
            t + k*a["ltv"] + target*((1+a["closing"]) - a["ltv"]*(1-a["points"])))
    else:
        raise ValueError("target_metric in {cap,dscr,coc}")
    return P

# ----------------------------------------------------------- portfolio DCF
def portfolio_dcf(p):
    """Port of fIRR LAMBDA + SFR cash flow. p = full assumption dict."""
    H, r0 = p["homes"], p["rent_home"]
    price = p["price_home"]; pr = H*price
    allin = pr*(1+p["acq"]) + H*p["rehab"]
    loan  = pr*p["ltv"]; eq = allin - loan + loan*p["loan_fee"]
    g, xg, vc = p["rent_growth"], p["exp_growth"], p["vacancy"]
    pm, rm = p["pm"], p["rm"]
    txp, ins, cap, oth, hoa = p["tax"], p["ins_home"], p["capex_home"], p["other_home"], p["hoa_home"]
    rt, am, io, hd, ec, sl = p["rate"], p["amort"], p["io_years"], p["hold"], p["exit_cap"], p["selling"]

    fixed0 = pr*txp + H*(ins+oth+hoa)
    t = np.arange(1, hd+1)
    egi = H*r0*12*(1+g)**(t-1)*(1-vc)
    noi = egi*(1-pm-rm) - fixed0*(1+xg)**(t-1)
    cfo = noi - H*cap*(1+xg)**(t-1)
    pmt = -npf.pmt(rt/12, am*12, loan)*12
    ds  = np.where(t <= io, loan*rt, pmt)
    # reversion on forward (year hd+1) NOI
    egiN = H*r0*12*(1+g)**hd*(1-vc)
    noiN = egiN*(1-pm-rm) - fixed0*(1+xg)**hd
    netrev = (noiN/ec)*(1-sl)
    # ending balance at hold
    i = rt/12; Nn = am*12; kk = max(0, hd-io)*12
    bal = loan if hd <= io else loan*((1+i)**Nn - (1+i)**kk)/((1+i)**Nn - 1)
    lev = cfo - ds
    lev[-1] += netrev - bal
    levered = np.concatenate([[-eq], lev])
    unlev = np.concatenate([[-allin], cfo.copy()]); unlev[-1] += netrev
    dscr = noi / ds
    return {
        "all_in": allin, "loan": loan, "equity": eq,
        "levered_irr": npf.irr(levered), "unlevered_irr": npf.irr(unlev),
        "equity_multiple": lev[lev>0].sum()/eq if eq else np.nan,
        "min_dscr": float(np.min(dscr)), "avg_dscr": float(np.mean(dscr)),
        "y1_noi": float(noi[0]), "going_in_cap": float(noi[0]/allin),
        "exit_value_gross": float(noiN/ec),
        "series": {
            "years": list(range(0, int(hd)+1)),
            "levered_cf": [round(float(x),0) for x in levered],
            "noi": [0]+[round(float(x),0) for x in noi],
            "dscr": [None]+[round(float(x),3) for x in dscr],
            "cfo": [0]+[round(float(x),0) for x in cfo],
            "reversion": float(netrev),
        },
        "sources_uses": {
            "purchase": float(pr), "acq_cost": float(pr*p["acq"]),
            "rehab": float(H*p["rehab"]), "loan_fee": float(loan*p["loan_fee"]),
            "loan": float(loan), "equity": float(eq),
        },
    }


if __name__ == "__main__":
    import json, os
    DATA = r"C:\Users\AshokReddy\Downloads\International\data"
    FX = json.load(open(os.path.join(DATA, "fix_params.json")))
    # ---- BASE (original assumptions) vs FIXED (per-state costs + discount)
    common = dict(homes=100, acq=0.02, rehab=5000, rent_growth=0.03, exp_growth=0.025,
                  vacancy=0.05, pm=0.08, rm=0.05, other_home=300, hoa_home=0,
                  ltv=0.70, rate=0.0725, amort=30, io_years=5, hold=7, exit_cap=0.065,
                  selling=0.02, loan_fee=0.01)
    base = portfolio_dcf({**common, "price_home":269160, "rent_home":2407,
                          "tax":0.011, "ins_home":1400, "capex_home":300})
    fixed = portfolio_dcf({**common,
                  "price_home": FX["tier1_avg_avm"]*FX["fixes"]["avm_discount"],
                  "rent_home":  FX["tier1_avg_marketrent"],
                  "tax": FX["blended_tax_pct"], "ins_home": int(FX["blended_insurance"]),
                  "capex_home": FX["capex_per_home"]})
    def show(t,r):
        print(f"\n{t}: levIRR={r['levered_irr']*100:.1f}%  EMx={r['equity_multiple']:.2f}  "
              f"minDSCR={r['min_dscr']:.2f}  goinCap={r['going_in_cap']*100:.2f}%  "
              f"unlevIRR={r['unlevered_irr']*100:.1f}%")
    show("BASE (original)", base)
    show("FIXED (per-state costs + 90% AVM basis + market rent)", fixed)

    # reverse-solver demo
    a = dict(closing=0.03, rehab=15000, vacancy=0.05, pm=0.08, maint=0.05,
             tax=0.011, ins=1400, hoa=0, other=300, ltv=0.70, rate=0.0725, amort=30, points=0.01)
    for tm, tgt in [("cap",0.07),("dscr",1.25),("coc",0.08)]:
        P = reverse_price(tm, tgt, a, 3200)
        chk = underwrite(P, 3200, a)
        print(f"reverse {tm}={tgt}: price=${P:,.0f}  -> check cap={chk['cap_rate']:.4f} "
              f"dscr={chk['dscr']:.3f} coc={chk['coc']:.4f}")
