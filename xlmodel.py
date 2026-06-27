"""
xlmodel.py — institutional-grade, LIVE-FORMULA Excel models.

Two builders, one DCF engine:
  • property_model(...)  — a very detailed single-asset model for one APN: Buy Box +
    Scoring (from the live trace) + a full 10-year DCF, amortization schedule, returns
    (levered/unlevered IRR, equity multiple, DSCR, caps), a sensitivity grid, and a
    scenario summary.
  • firm_model(...)      — a faithful replica of the firm's workbook (minus Reference
    sheets): Exec Summary, Dashboard, Buy Box, Methodology, Scoring Model, Tiers, the
    two-way Underwriting tool, and the SFR Model (Assumptions / Cash Flow / Returns).

Both pull the buy-box bands, weights and assumptions from the PROJECT's Model Studio
profile, and write real Excel formulas (with cached results) so the workbook recomputes
on edit — change a rate or a growth rate and IRR/DSCR move.
"""
import io
import numpy_financial as npf
import xlsxwriter
from xlsxwriter.utility import xl_rowcol_to_cell as CELL
from reports import _xl_formats, _num
import knowledge


# ----------------------------------------------------------- DCF compute (mirrors re_underwrite.portfolio_dcf)
def compute(p):
    H, P, R0 = p["homes"], p["price_home"], p["rent_home"]
    pr = H * P
    all_in = pr * (1 + p["acq"]) + H * p["rehab"]
    loan = pr * p["ltv"]; eq = all_in - loan + loan * p["loan_fee"]
    g, xg, vc, pm, rm = p["rent_growth"], p["exp_growth"], p["vacancy"], p["pm"], p["rm"]
    fixed0 = pr * p["tax"] + H * (p["ins_home"] + p["other_home"] + p["hoa_home"])
    hold, io = int(p["hold"]), int(p["io_years"])
    rate, am = p["rate"], int(p["amort"])
    i, Nn = rate / 12, am * 12
    pmt = -npf.pmt(i, Nn, loan) * 12
    EGI, NOI, CAP, CFO, DS, LEV, UNLEV, DSCR = [0], [0], [0], [0], [0], [-eq], [-all_in], [None]
    for t in range(1, hold + 1):
        egi = H * R0 * 12 * (1 + g) ** (t - 1) * (1 - vc)
        noi = egi * (1 - pm - rm) - fixed0 * (1 + xg) ** (t - 1)
        cap = H * p["capex_home"] * (1 + xg) ** (t - 1)
        cfo = noi - cap
        ds = loan * rate if t <= io else pmt
        EGI.append(egi); NOI.append(noi); CAP.append(cap); CFO.append(cfo); DS.append(ds)
        DSCR.append(noi / ds if ds else None); LEV.append(cfo - ds); UNLEV.append(cfo)
    egiN = H * R0 * 12 * (1 + g) ** hold * (1 - vc)
    noiN = egiN * (1 - pm - rm) - fixed0 * (1 + xg) ** hold
    gross_rev = noiN / p["exit_cap"]; net_rev = gross_rev * (1 - p["selling"])
    kk = max(0, hold - io) * 12
    bal = loan if hold <= io else loan * ((1 + i) ** Nn - (1 + i) ** kk) / ((1 + i) ** Nn - 1)
    LEV[-1] += net_rev - bal; UNLEV[-1] += net_rev
    dscr_vals = [d for d in DSCR if d]
    return {"all_in": all_in, "purchase": pr, "loan": loan, "equity": eq, "fixed0": fixed0,
            "pmt": pmt, "net_rev": net_rev, "gross_rev": gross_rev, "ending_bal": bal,
            "EGI": EGI, "NOI": NOI, "CAP": CAP, "CFO": CFO, "DS": DS, "LEV": LEV, "UNLEV": UNLEV,
            "DSCR": DSCR, "lev_irr": float(npf.irr(LEV)), "unlev_irr": float(npf.irr(UNLEV)),
            "emx": sum(x for x in LEV[1:] if x > 0) / eq if eq else 0,
            "min_dscr": min(dscr_vals) if dscr_vals else 0,
            "avg_dscr": sum(dscr_vals) / len(dscr_vals) if dscr_vals else 0,
            "going_cap": NOI[1] / all_in if all_in else 0, "y1_noi": NOI[1], "hold": hold, "io": io}


# ----------------------------------------------------------- shared formats
def _fmts(wb):
    F = _xl_formats(wb)
    F["fx"] = wb.add_format({"font_size": 9, "font_color": "#5a6a55", "italic": True, "border": 1,
                             "border_color": "#e4e8f0", "font_name": "Consolas"})
    F["inp"] = wb.add_format({"font_size": 10, "border": 1, "border_color": "#c9d6cf",
                              "bg_color": "#f0fff8", "align": "right", "num_format": "0.00"})
    F["inpM"] = wb.add_format({"font_size": 10, "border": 1, "border_color": "#c9d6cf",
                               "bg_color": "#f0fff8", "align": "right", "num_format": "$#,##0"})
    F["inpP"] = wb.add_format({"font_size": 10, "border": 1, "border_color": "#c9d6cf",
                               "bg_color": "#f0fff8", "align": "right", "num_format": "0.00%"})
    F["lbl"] = wb.add_format({"font_size": 10, "border": 1, "border_color": "#e4e8f0", "bold": True})
    F["lblr"] = wb.add_format({"font_size": 10, "border": 1, "border_color": "#e4e8f0"})
    F["sec"] = wb.add_format({"bold": True, "font_size": 12, "font_color": "white", "bg_color": "#0e9d6e"})
    F["secd"] = wb.add_format({"bold": True, "font_size": 12, "font_color": "white", "bg_color": "#0b1320"})
    F["tot"] = wb.add_format({"bold": True, "font_size": 10, "border": 1, "border_color": "#e4e8f0",
                              "bg_color": "#eef4f1", "num_format": "$#,##0", "align": "right"})
    F["totp"] = wb.add_format({"bold": True, "font_size": 10, "border": 1, "border_color": "#e4e8f0",
                               "bg_color": "#eef4f1", "num_format": "0.00%", "align": "right"})
    F["passC"] = wb.add_format({"font_size": 10, "border": 1, "border_color": "#e4e8f0",
                                "align": "center", "bold": True, "font_color": "#0a7a55"})
    return F


def _defname(wb, ws, r, c, name):
    try: wb.define_name(name, "='%s'!$%s$%d" % (ws.name, CELL(r, c)[:-len(str(r + 1))], r + 1))
    except Exception: pass


# ----------------------------------------------------------- Assumptions sheet (named cells)
def _assumptions_sheet(wb, F, p, profile, assumptions, kind):
    A = wb.add_worksheet("Assumptions"); A.hide_gridlines(2)
    A.set_column(0, 0, 30); A.set_column(1, 1, 15); A.set_column(2, 2, 3)
    A.set_column(3, 3, 30); A.set_column(4, 4, 15)
    A.merge_range(0, 0, 0, 4, "Working Model — Assumptions (green = inputs)", F["title"])
    A.merge_range(1, 0, 1, 4, "Edit any green cell; the Cash Flow, Returns and Underwriting sheets recompute.", F["sub"])
    fmt = {"money": "inpM", "pct": "inpP", "num": "inp"}
    def block(col, title, rows, r0=3):
        A.write(r0, col, title, F["sec"] if col == 0 else F["secd"])
        for i, (nm, lb, v, k) in enumerate(rows):
            A.write(r0 + 1 + i, col, lb, F["lbl"]); A.write(r0 + 1 + i, col + 1, v, F[fmt[k]])
            _defname(wb, A, r0 + 1 + i, col + 1, nm)
        return r0 + 1 + len(rows)
    dcf = [("homes", "Homes in model", p["homes"], "num"),
           ("price_home", "Price / home (offer)", p["price_home"], "money"),
           ("rent_home", "Monthly rent / home", p["rent_home"], "money"),
           ("acq", "Acquisition cost %", p["acq"], "pct"),
           ("rehab", "Rehab / home", p["rehab"], "money"),
           ("loan_fee", "Loan fee %", p["loan_fee"], "pct"),
           ("rent_growth", "Rent growth (annual)", p["rent_growth"], "pct"),
           ("exp_growth", "Expense growth (annual)", p["exp_growth"], "pct"),
           ("vacancy", "Vacancy", p["vacancy"], "pct"),
           ("pm", "Property mgmt %", p["pm"], "pct"),
           ("rm", "Repairs & maint %", p["rm"], "pct"),
           ("tax", "Property tax %", p["tax"], "pct"),
           ("ins_home", "Insurance / home", p["ins_home"], "money"),
           ("capex_home", "Capex reserve / home", p["capex_home"], "money"),
           ("other_home", "Other opex / home", p["other_home"], "money"),
           ("hoa_home", "HOA / home", p["hoa_home"], "money")]
    fin = [("ltv", "LTV", p["ltv"], "pct"),
           ("rate", "Interest rate", p["rate"], "pct"),
           ("amort", "Amortization (yrs)", p["amort"], "num"),
           ("io_years", "Interest-only years", p["io_years"], "num"),
           ("hold", "Hold (yrs)", p["hold"], "num"),
           ("exit_cap", "Exit cap rate", p["exit_cap"], "pct"),
           ("selling", "Selling cost %", p["selling"], "pct")]
    block(0, "Operating assumptions", dcf)
    block(3, "Capital structure & exit", fin)
    # derived (named, live)
    rr = 3 + len(fin) + 2
    A.write(rr, 3, "Derived", F["secd"])
    der = [("purchase", "Purchase price", "=homes*price_home"),
           ("all_in", "All-in basis", "=purchase*(1+acq)+homes*rehab"),
           ("loan", "Senior loan", "=purchase*ltv"),
           ("equity", "Equity required", "=all_in-loan+loan*loan_fee"),
           ("fixed0", "Yr-1 fixed opex", "=purchase*tax+homes*(ins_home+other_home+hoa_home)"),
           ("pmt_annual", "Amortizing debt svc", "=-PMT(rate/12,amort*12,loan)*12")]
    comp = compute(p)
    cache = {"purchase": comp["purchase"], "all_in": comp["all_in"], "loan": comp["loan"],
             "equity": comp["equity"], "fixed0": comp["fixed0"], "pmt_annual": comp["pmt"]}
    for i, (nm, lb, f) in enumerate(der):
        A.write(rr + 1 + i, 3, lb, F["lbl"]); A.write_formula(rr + 1 + i, 4, f, F["tot"], cache[nm])
        _defname(wb, A, rr + 1 + i, 4, nm)
    return comp


# ----------------------------------------------------------- Cash Flow (years across columns, live)
def _cashflow_sheet(wb, F, comp, p, name="Cash Flow", title="Cash Flow — 10-year levered DCF"):
    hold = int(p["hold"]); io = int(p["io_years"])
    ws = wb.add_worksheet(name); ws.hide_gridlines(2)
    ws.set_column(0, 0, 26); ws.set_column(1, hold + 1, 13)
    ws.merge_range(0, 0, 0, hold + 1, title, F["title"])
    ws.merge_range(1, 0, 1, hold + 1, "Per-year operating cash flow → levered cash flow (with reversion in the final year).", F["sub"])
    hr = 3
    ws.write(hr, 0, "Line item", F["hdr"])
    for t in range(hold + 1): ws.write(hr, 1 + t, "Year %d" % t, F["hdrR"])
    rows = [("EGI", "Effective gross income", "EGI", "money"),
            ("NOI", "Net operating income", "NOI", "money"),
            ("CAP", "Capex reserve", "CAP", "money"),
            ("CFO", "Cash flow from ops", "CFO", "money"),
            ("DS", "Debt service", "DS", "money"),
            ("LEV", "Levered cash flow", "LEV", "tot"),
            ("DSCR", "DSCR", "DSCR", "num2")]
    ri = {k: hr + 1 + i for i, (k, _, _, _) in enumerate(rows)}
    for k, lbl, _, _ in rows: ws.write(ri[k], 0, lbl, F["lbl"])
    def c(key, t): return CELL(ri[key], 1 + t)
    for t in range(hold + 1):
        col = 1 + t
        if t == 0:
            ws.write_blank(ri["EGI"], col, None, F["money"]); ws.write_blank(ri["NOI"], col, None, F["money"])
            ws.write_blank(ri["CAP"], col, None, F["money"]); ws.write_blank(ri["CFO"], col, None, F["money"])
            ws.write_blank(ri["DS"], col, None, F["money"])
            ws.write_formula(ri["LEV"], col, "=-equity", F["tot"], comp["LEV"][0])
            ws.write_blank(ri["DSCR"], col, None, F["num2"]); continue
        ws.write_formula(ri["EGI"], col, "=homes*rent_home*12*POWER(1+rent_growth,%d-1)*(1-vacancy)" % t, F["money"], comp["EGI"][t])
        ws.write_formula(ri["NOI"], col, "=%s*(1-pm-rm)-fixed0*POWER(1+exp_growth,%d-1)" % (c("EGI", t), t), F["money"], comp["NOI"][t])
        ws.write_formula(ri["CAP"], col, "=homes*capex_home*POWER(1+exp_growth,%d-1)" % t, F["money"], comp["CAP"][t])
        ws.write_formula(ri["CFO"], col, "=%s-%s" % (c("NOI", t), c("CAP", t)), F["money"], comp["CFO"][t])
        ws.write_formula(ri["DS"], col, "=IF(%d<=io_years,loan*rate,pmt_annual)" % t, F["money"], comp["DS"][t])
        lev = "=%s-%s" % (c("CFO", t), c("DS", t))
        if t == hold: lev += "+net_reversion-ending_balance"
        ws.write_formula(ri["LEV"], col, lev, F["tot"], comp["LEV"][t])
        ws.write_formula(ri["DSCR"], col, "=%s/%s" % (c("NOI", t), c("DS", t)), F["num2"], comp["DSCR"][t])
    # reversion block (named)
    rr = ri["DSCR"] + 2
    ws.write(rr, 0, "Reversion (exit)", F["secd"])
    for i, (nm, lbl, f, val) in enumerate([
        ("exit_noi", "Forward NOI (yr %d)" % (hold + 1), "=homes*rent_home*12*POWER(1+rent_growth,hold)*(1-vacancy)*(1-pm-rm)-fixed0*POWER(1+exp_growth,hold)", comp["NOI"][-1] if False else None),
        ("gross_reversion", "Gross sale value", "=exit_noi/exit_cap", comp["gross_rev"]),
        ("net_reversion", "Net of selling cost", "=gross_reversion*(1-selling)", comp["net_rev"]),
        ("ending_balance", "Loan payoff at exit", None, comp["ending_bal"])]):
        ws.write(rr + 1 + i, 0, lbl, F["lbl"])
        if f: ws.write_formula(rr + 1 + i, 1, f, F["tot"], val if val is not None else 0)
        else: ws.write(rr + 1 + i, 1, val, F["tot"])
        _defname(wb, ws, rr + 1 + i, 1, nm)
    # exit_noi cached
    exit_noi_val = (p["homes"] * p["rent_home"] * 12 * (1 + p["rent_growth"]) ** hold * (1 - p["vacancy"]) * (1 - p["pm"] - p["rm"]) - comp["fixed0"] * (1 + p["exp_growth"]) ** hold)
    ws.write_formula(rr + 1, 1, "=homes*rent_home*12*POWER(1+rent_growth,hold)*(1-vacancy)*(1-pm-rm)-fixed0*POWER(1+exp_growth,hold)", F["tot"], exit_noi_val)
    return ri, name


# ----------------------------------------------------------- Returns sheet
def _returns_sheet(wb, F, comp, p, ri, cf_name, scenarios):
    hold = int(p["hold"])
    ws = wb.add_worksheet("Returns"); ws.hide_gridlines(2)
    ws.set_column(0, 0, 30); ws.set_column(1, 1, 16); ws.set_column(2, 2, 50)
    ws.merge_range(0, 0, 0, 2, "Returns & coverage", F["title"])
    ws.merge_range(1, 0, 1, 2, "Computed live from the Cash Flow sheet.", F["sub"])
    lev_rng = "'%s'!%s:%s" % (cf_name, CELL(ri["LEV"], 1), CELL(ri["LEV"], 1 + hold))
    dscr_rng = "'%s'!%s:%s" % (cf_name, CELL(ri["DSCR"], 2), CELL(ri["DSCR"], 1 + hold))
    metrics = [("Levered IRR", "=IRR(%s)" % lev_rng, comp["lev_irr"], "totp"),
               ("Equity multiple", "=SUMIF(%s,\">0\")/equity" % lev_rng, comp["emx"], "num2"),
               ("Unlevered IRR (approx)", None, comp["unlev_irr"], "totp"),
               ("Going-in cap rate", "='%s'!%s/all_in" % (cf_name, CELL(ri["NOI"], 2)), comp["going_cap"], "totp"),
               ("Year-1 NOI", "='%s'!%s" % (cf_name, CELL(ri["NOI"], 2)), comp["y1_noi"], "tot"),
               ("Min DSCR", "=MIN(%s)" % dscr_rng, comp["min_dscr"], "num2"),
               ("Avg DSCR", "=AVERAGE(%s)" % dscr_rng, comp["avg_dscr"], "num2"),
               ("Equity invested", "=equity", comp["equity"], "tot"),
               ("All-in basis", "=all_in", comp["all_in"], "tot"),
               ("Exit value (gross)", "=gross_reversion", comp["gross_rev"], "tot")]
    r = 3
    ws.write(r, 0, "Metric", F["hdr"]); ws.write(r, 1, "Value", F["hdrR"]); ws.write(r, 2, "Basis", F["hdr"]); r += 1
    for lbl, f, val, fmt in metrics:
        ws.write(r, 0, lbl, F["lbl"])
        if f: ws.write_formula(r, 1, f, F[fmt], val)
        else: ws.write(r, 1, val, F[fmt])
        r += 1
    # scenario summary (computed)
    r += 1; ws.write(r, 0, "Scenario summary", F["sec"]); r += 1
    ws.write(r, 0, "Scenario", F["hdr"]); ws.write(r, 1, "Lev IRR", F["hdrR"]); ws.write(r, 2, "EMx / Min DSCR", F["hdr"]); r += 1
    for sc, dv in (scenarios or {"Base": {}}).items():
        cc = compute({**p, **dv})
        ws.write(r, 0, sc, F["lblr"]); ws.write(r, 1, cc["lev_irr"], F["totp"])
        ws.write(r, 2, "%.2fx  ·  min DSCR %.2f" % (cc["emx"], cc["min_dscr"]), F["lblr"]); r += 1
    # sensitivity grid: Levered IRR vs exit cap (rows) × rate (cols)
    r += 1; ws.write(r, 0, "Sensitivity — Levered IRR (exit cap ↓ vs rate →)", F["sec"]); r += 1
    rates = [p["rate"] - 0.01, p["rate"] - 0.005, p["rate"], p["rate"] + 0.005, p["rate"] + 0.01]
    caps = [p["exit_cap"] - 0.01, p["exit_cap"] - 0.005, p["exit_cap"], p["exit_cap"] + 0.005, p["exit_cap"] + 0.01]
    ws.write(r, 0, "exit cap \\ rate", F["hdr"])
    for j, rt in enumerate(rates): ws.write(r, 1 + j, rt, F["pct2"])
    r += 1
    for ec in caps:
        ws.write(r, 0, ec, F["pct2"])
        for j, rt in enumerate(rates):
            cc = compute({**p, "exit_cap": ec, "rate": rt})
            ws.write(r, 1 + j, cc["lev_irr"], F["pct2"])
        r += 1


# ----------------------------------------------------------- Amortization schedule
def _amort_sheet(wb, F, p):
    hold = int(p["hold"]); io = int(p["io_years"]); am = int(p["amort"]); rate = p["rate"]
    comp = compute(p); loan = comp["loan"]; i = rate / 12; Nn = am * 12; pmt = comp["pmt"]
    ws = wb.add_worksheet("Amortization"); ws.hide_gridlines(2)
    for cc, w in [(0, 10), (1, 16), (2, 14), (3, 14), (4, 16)]: ws.set_column(cc, cc, w)
    ws.merge_range(0, 0, 0, 4, "Loan amortization schedule", F["title"])
    ws.merge_range(1, 0, 1, 4, "%d interest-only years, then amortizing over %d years." % (io, am), F["sub"])
    for cc, h in enumerate(["Year", "Beginning balance", "Interest", "Principal", "Ending balance"]):
        ws.write(3, cc, h, F["hdrR"] if cc else F["hdr"])
    bal = loan; r = 4
    for t in range(1, hold + 1):
        begin = bal
        if t <= io:
            interest = bal * rate; principal = 0.0; end = bal
            ws.write(r, 2, interest, F["money"]); ws.write(r, 3, principal, F["money"])
        else:
            s = (t - io - 1) * 12 + 1; e = (t - io) * 12
            interest = -npf.ipmt(i, range(s, e + 1), Nn, loan).sum()
            principal = -npf.ppmt(i, range(s, e + 1), Nn, loan).sum()
            end = begin - principal
            ws.write_formula(r, 2, "=-CUMIPMT(rate/12,amort*12,loan,%d,%d,0)" % (s, e), F["money"], interest)
            ws.write_formula(r, 3, "=-CUMPRINC(rate/12,amort*12,loan,%d,%d,0)" % (s, e), F["money"], principal)
        ws.write(r, 0, "Year %d" % t, F["lblr"]); ws.write(r, 1, begin, F["money"]); ws.write(r, 4, end, F["money"])
        bal = end; r += 1


# ----------------------------------------------------------- Buy Box + Scoring (from the trace)
def _gate_scoring_sheets(wb, F, trace, profile):
    g = trace["gate"]; s = trace["score"]
    G = wb.add_worksheet("Buy Box"); G.hide_gridlines(2)
    G.set_column(0, 0, 26); G.set_column(1, 1, 16); G.set_column(2, 2, 46); G.set_column(3, 3, 10)
    G.merge_range(0, 0, 0, 3, "Buy Box gate — hard filter", F["title"])
    G.merge_range(1, 0, 1, 3, "Bands from this project's Model Studio. Fail any line ⇒ Not a Match.", F["sub"])
    for cidx, h in enumerate(["Criterion", "Subject", "Test", "Result"]): G.write(3, cidx, h, F["hdr"])
    r = 4
    for row in g["rows"]:
        G.write(r, 0, row["label"], F["lbl"]); G.write(r, 1, row["value"], F["lblr"])
        G.write(r, 2, row["formula"], F["fx"]); G.write(r, 3, "PASS" if row["pass"] else "FAIL", F["passC"]); r += 1
    G.write(r + 1, 0, "GATE", F["secd"]); G.merge_range(r + 1, 1, r + 1, 3, g["verdict"], F["passC"])

    S = wb.add_worksheet("Scoring Model"); S.hide_gridlines(2)
    for cidx, w in [(0, 12), (1, 22), (2, 12), (3, 48), (4, 8), (5, 8), (6, 10)]: S.set_column(cidx, cidx, w)
    S.merge_range(0, 0, 0, 6, "Scoring model — four pillars", F["title"])
    S.merge_range(1, 0, 1, 6, "Weights from this project's Model Studio. sub-score × weight = contribution.", F["sub"])
    for cidx, h in enumerate(["Pillar", "Metric", "Input", "Normalization", "Sub", "Weight", "Contrib"]):
        S.write(3, cidx, h, F["hdr"] if cidx < 4 else F["hdrR"])
    r = 4
    for m in s["rows"]:
        S.write(r, 0, m["pillar"], F["cell"]); S.write(r, 1, m["metric"], F["cell"])
        S.write(r, 2, m["input"], F["cellR"]); S.write(r, 3, m["formula"], F["fx"])
        S.write(r, 4, m["subscore"], F["num2"]); S.write(r, 5, m["weight"], F["num2"]); S.write(r, 6, m["contribution"], F["num2"]); r += 1
    r += 1
    for lbl, val, fmt in [("Raw score", s["raw_score"], "num2"), ("Risk haircut", s["haircut"], "num2"),
                          ("TOTAL SCORE", s["total"], "num2"), ("TIER", s["tier"], "passC")]:
        S.write(r, 1, lbl, F["lbl"] if "TOTAL" not in lbl and "TIER" not in lbl else F["secd"])
        S.write(r, 6 if fmt != "passC" else 3, val, F[fmt]); r += 1


# ----------------------------------------------------------- builders
def property_model(prop, profile, assumptions, loc, trace, params, scenarios):
    buf = io.BytesIO(); wb = xlsxwriter.Workbook(buf, {"in_memory": True}); F = _fmts(wb)
    cov = wb.add_worksheet("Cover"); cov.hide_gridlines(2)
    cov.set_column(0, 0, 22); cov.set_column(1, 1, 50)
    cov.merge_range(0, 0, 0, 1, "Terra · Single-Asset Investment Model", F["title"])
    cov.merge_range(1, 0, 1, 1, "%s — %s, %s %s" % (prop.get("address", ""), prop.get("city", ""), prop.get("state", ""), prop.get("zip", "")), F["sub"])
    facts = [("APN", prop.get("apn")), ("Tier", trace["score"]["tier"]),
             ("Total score", round(trace["score"]["total"], 1)), ("AVM", _num(prop.get("avm"))),
             ("Market rent", _num(prop.get("market_rent"))), ("Offer (price/home)", round(params["price_home"]))]
    for i, (k, v) in enumerate(facts):
        cov.write(3 + i, 0, k, F["lbl"]); cov.write(3 + i, 1, v, F["lblr"])
    cov.merge_range(3 + len(facts) + 1, 0, 3 + len(facts) + 1, 1,
                    "Sheets: Assumptions · Buy Box · Scoring Model · Cash Flow · Amortization · Returns. "
                    "Green cells are inputs; everything else is live formulas.", F["sub"])
    comp = _assumptions_sheet(wb, F, params, profile, assumptions, "property")
    _gate_scoring_sheets(wb, F, trace, profile)
    ri, cf_name = _cashflow_sheet(wb, F, comp, params, title="Cash Flow — %d-year single-asset DCF" % int(params["hold"]))
    _amort_sheet(wb, F, params)
    _returns_sheet(wb, F, comp, params, ri, cf_name, scenarios)
    cov.activate()
    wb.close(); buf.seek(0); return buf.read()


def firm_model(profile, assumptions, snapshot, params, rep, loc, trace, scenarios, dcf=None):
    buf = io.BytesIO(); wb = xlsxwriter.Workbook(buf, {"in_memory": True}); F = _fmts(wb)
    # Exec Summary
    E = wb.add_worksheet("Exec Summary"); E.hide_gridlines(2); E.set_column(0, 0, 30); E.set_column(1, 1, 22)
    E.merge_range(0, 0, 0, 3, "Terra · %s — Acquisition Model" % profile.get("label", "SFR"), F["title"])
    E.merge_range(1, 0, 1, 3, "Replica of the firm model (reference tables excluded). Buy box & assumptions from Model Studio.", F["sub"])
    comp = compute(params)
    kp = [("Tier-1 targets", snapshot["tiers"].get("Tier 1 - Strong", 0)),
          ("Match rate", snapshot.get("match_rate", 0)),
          ("Portfolio levered IRR", comp["lev_irr"]), ("Equity multiple", comp["emx"]),
          ("Min DSCR", comp["min_dscr"]), ("Going-in cap", comp["going_cap"]),
          ("Equity required", comp["equity"]), ("All-in basis", comp["all_in"])]
    for i, (k, v) in enumerate(kp):
        E.write(3 + i, 0, k, F["lbl"])
        fmt = "pct" if ("rate" in k.lower() or "IRR" in k or "cap" in k.lower()) else ("num2" if "DSCR" in k or "multiple" in k else ("money" if "Equity" in k or "basis" in k else "cellR"))
        E.write(3 + i, 1, v, F.get(fmt, F["cellR"]))
    # Dashboard (tier distribution)
    D = wb.add_worksheet("Dashboard"); D.hide_gridlines(2); D.set_column(0, 0, 24); D.set_column(1, 1, 14)
    D.merge_range(0, 0, 0, 2, "Dashboard — pipeline", F["title"])
    D.write(2, 0, "Tier", F["hdr"]); D.write(2, 1, "Count", F["hdrR"]); rr = 3
    for t in ["Tier 1 - Strong", "Tier 2 - Moderate", "Tier 3 - Watch", "Not a Match"]:
        D.write(rr, 0, t, F["cell"]); D.write(rr, 1, snapshot["tiers"].get(t, 0), F["cellR"]); rr += 1
    chart = wb.add_chart({"type": "column"})
    chart.add_series({"categories": ["Dashboard", 3, 0, 6, 0], "values": ["Dashboard", 3, 1, 6, 1], "fill": {"color": "#0e9d6e"}})
    chart.set_legend({"none": True}); chart.set_title({"name": "Pipeline by tier"}); chart.set_size({"width": 460, "height": 240})
    D.insert_chart(2, 3, chart)
    # Methodology
    M = wb.add_worksheet("Methodology"); M.hide_gridlines(2); M.set_column(0, 0, 110)
    M.write(0, 0, "Methodology", F["title"])
    spec = knowledge.model_spec(profile, assumptions, snapshot)
    for i, line in enumerate([l for l in spec.split("\n") if l.strip()][:60]):
        M.write(2 + i, 0, line, F["fx"] if line.strip().startswith(("•", "-", "raw", "RISK", "TIERS")) else F["lblr"])
    # Buy Box + Scoring (from trace of the representative deal) + SFR Model (DCF)
    _gate_scoring_sheets(wb, F, trace, profile)
    comp2 = _assumptions_sheet(wb, F, params, profile, assumptions, "firm")
    ri, cf_name = _cashflow_sheet(wb, F, comp2, params, name="SFR Model — Cash Flow",
                                  title="SFR Model — %d-year portfolio DCF (%d homes)" % (int(params["hold"]), int(params["homes"])))
    _returns_sheet(wb, F, comp2, params, ri, cf_name, scenarios)
    E.activate()
    wb.close(); buf.seek(0); return buf.read()
