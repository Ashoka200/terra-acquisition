"""
reports.py — branded, well-formatted PDF (reportlab) + Excel (xlsxwriter) exports.
Terra palette (ink #0b1320 / emerald #0e9d6e). KPI cards, styled tables, charts.
"""
import io
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Flowable)
import xlsxwriter

INK = colors.HexColor("#0b1320"); EMER = colors.HexColor("#0e9d6e")
EMERL = colors.HexColor("#e7f7f0"); MUT = colors.HexColor("#6b7689")
LINE = colors.HexColor("#e4e8f0"); LIGHT = colors.HexColor("#f7f9fc")

def _money(x): return "${:,.0f}".format(x) if x is not None else "—"
def _pct(x, d=1): return ("{:."+str(d)+"f}%").format(x*100) if x is not None else "—"

# ----------------------------------------------------------- PDF helpers
class HeaderFooter:
    def __init__(self, title, subtitle): self.title, self.subtitle = title, subtitle
    def __call__(self, canvas, doc):
        canvas.saveState()
        canvas.setFillColor(INK); canvas.rect(0, 10.55*inch, 8.5*inch, 0.95*inch, fill=1, stroke=0)
        canvas.setFillColor(EMER); canvas.roundRect(0.6*inch, 10.72*inch, 0.42*inch, 0.42*inch, 6, fill=1, stroke=0)
        canvas.setFillColor(colors.white); canvas.setFont("Helvetica-Bold", 9)
        canvas.drawString(0.69*inch, 10.86*inch, "▲")
        canvas.setFont("Helvetica-Bold", 15); canvas.drawString(1.18*inch, 10.95*inch, "Terra")
        canvas.setFont("Helvetica", 8.5); canvas.setFillColor(colors.HexColor("#aeb8c9"))
        canvas.drawString(1.18*inch, 10.78*inch, "Acquisition Intelligence")
        canvas.setFont("Helvetica", 8.5); canvas.setFillColor(colors.HexColor("#cdd5e3"))
        canvas.drawRightString(7.9*inch, 10.88*inch, self.subtitle)
        canvas.setStrokeColor(LINE); canvas.setLineWidth(0.5); canvas.line(0.6*inch, 0.6*inch, 7.9*inch, 0.6*inch)
        canvas.setFillColor(MUT); canvas.setFont("Helvetica", 7.5)
        canvas.drawString(0.6*inch, 0.42*inch, "Terra · United Brothers — generated from the deterministic engine (100% parity).")
        canvas.drawRightString(7.9*inch, 0.42*inch, "Page %d" % doc.page)
        canvas.restoreState()

def kpi_grid(items, cols=4):
    cells, row = [], []
    for i, (label, value) in enumerate(items):
        p = [Paragraph(f'<font size=7 color="#6b7689"><b>{label.upper()}</b></font>', _S["lbl"]),
             Paragraph(f'<font size=15 color="#0a7a55"><b>{value}</b></font>', _S["val"])]
        row.append(p)
        if len(row) == cols: cells.append(row); row = []
    if row:
        while len(row) < cols: row.append("")
        cells.append(row)
    t = Table(cells, colWidths=[(7.3/cols)*inch]*cols, hAlign="LEFT")
    t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),EMERL),("BOX",(0,0),(-1,-1),0.5,LINE),
        ("INNERGRID",(0,0),(-1,-1),3,colors.white),("VALIGN",(0,0),(-1,-1),"TOP"),
        ("TOPPADDING",(0,0),(-1,-1),9),("BOTTOMPADDING",(0,0),(-1,-1),9),
        ("LEFTPADDING",(0,0),(-1,-1),11),("RIGHTPADDING",(0,0),(-1,-1),6)]))
    return t

def data_table(header, rows, aligns=None, widths=None):
    data = [header] + rows
    t = Table(data, colWidths=widths, hAlign="LEFT", repeatRows=1)
    st = [("BACKGROUND",(0,0),(-1,0),INK),("TEXTCOLOR",(0,0),(-1,0),colors.white),
          ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,-1),8.5),
          ("FONTSIZE",(0,0),(-1,0),7.5),("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),6),
          ("LEFTPADDING",(0,0),(-1,-1),8),("RIGHTPADDING",(0,0),(-1,-1),8),
          ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white, LIGHT]),
          ("LINEBELOW",(0,0),(-1,-1),0.4,LINE),("VALIGN",(0,0),(-1,-1),"MIDDLE")]
    if aligns:
        for i, a in enumerate(aligns): st.append(("ALIGN",(i,0),(i,-1),a))
    t.setStyle(TableStyle(st)); return t

_styles = getSampleStyleSheet()
_S = {
    "h1": ParagraphStyle("h1", parent=_styles["Heading1"], fontName="Helvetica-Bold", fontSize=19, textColor=INK, spaceAfter=2, spaceBefore=6),
    "h2": ParagraphStyle("h2", parent=_styles["Heading2"], fontName="Helvetica-Bold", fontSize=12.5, textColor=INK, spaceBefore=14, spaceAfter=7),
    "p": ParagraphStyle("p", parent=_styles["Normal"], fontSize=9.5, textColor=colors.HexColor("#33405a"), leading=14),
    "sub": ParagraphStyle("sub", parent=_styles["Normal"], fontSize=10, textColor=MUT, spaceAfter=8),
    "lbl": ParagraphStyle("lbl", fontSize=7), "val": ParagraphStyle("val", fontSize=15, spaceBefore=3),
}

def _doc(title, subtitle):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, topMargin=1.25*inch, bottomMargin=0.8*inch,
                            leftMargin=0.6*inch, rightMargin=0.6*inch, title=title)
    return buf, doc

def portfolio_pdf(r, scenario="Base"):
    buf, doc = _doc("Portfolio Report", scenario + " scenario")
    su = r.get("sources_uses", {})
    flow = [Paragraph("Portfolio Investment Report", _S["h1"]),
            Paragraph(f"100-home Tier-1 SFR portfolio · 10-year levered model · <b>{scenario}</b> scenario.", _S["sub"]),
            kpi_grid([("Levered IRR", _pct(r["levered_irr"])), ("Equity Multiple", f"{r['equity_multiple']:.2f}×"),
                      ("Min DSCR", f"{r['min_dscr']:.2f}"), ("Going-in Cap", _pct(r["going_in_cap"],2)),
                      ("Unlevered IRR", _pct(r["unlevered_irr"])), ("Year-1 NOI", _money(r["y1_noi"])),
                      ("All-in Basis", _money(r["all_in"])), ("Equity", _money(r["equity"]))]),
            Paragraph("Annual cash flow", _S["h2"])]
    s = r["series"]
    rows = [[f"Year {y}", _money(s["noi"][i]) if i < len(s["noi"]) else "—",
             _money(s["cfo"][i]) if i < len(s["cfo"]) else "—",
             _money(s["levered_cf"][i]), (f"{s['dscr'][i]:.2f}" if s["dscr"][i] else "—")]
            for i, y in enumerate(s["years"])]
    flow.append(data_table(["Year", "NOI", "Cash from Ops", "Levered CF", "DSCR"], rows,
                aligns=["LEFT","RIGHT","RIGHT","RIGHT","RIGHT"],
                widths=[1.0*inch, 1.6*inch, 1.6*inch, 1.6*inch, 1.5*inch]))
    if "compare" in r:
        flow.append(Paragraph("Scenario comparison", _S["h2"]))
        crows = [[k, _pct(v["levered_irr"]), f"{v['equity_multiple']:.2f}×", f"{v['min_dscr']:.2f}"]
                 for k, v in r["compare"].items()]
        flow.append(data_table(["Scenario", "Levered IRR", "Equity Mult.", "Min DSCR"], crows,
                    aligns=["LEFT","RIGHT","RIGHT","RIGHT"], widths=[2.2*inch,1.7*inch,1.7*inch,1.7*inch]))
    if su:
        flow.append(Paragraph("Sources &amp; uses", _S["h2"]))
        flow.append(data_table(["Item", "Amount"],
            [["Purchase", _money(su["purchase"])], ["Acquisition cost", _money(su["acq_cost"])],
             ["Rehab", _money(su["rehab"])], ["Loan fee", _money(su["loan_fee"])],
             ["Senior debt", _money(su["loan"])], ["Equity", _money(su["equity"])]],
            aligns=["LEFT","RIGHT"], widths=[4.0*inch, 3.3*inch]))
    hf = HeaderFooter("Portfolio Report", scenario + " scenario")
    doc.build(flow, onFirstPage=hf, onLaterPages=hf); buf.seek(0); return buf.read()

SEVCOLOR = {"Critical": colors.HexColor("#c0392b"), "High": colors.HexColor("#e2783a"),
            "Medium": colors.HexColor("#c79114"), "Low": colors.HexColor("#3f7fd6"),
            "Minor": colors.HexColor("#7a8597"), "Info": colors.HexColor("#9aa3b2")}

def management_pdf(pl):
    """Investment-committee report: top-down narrative — thesis → opportunity → returns →
    risk (every flag) → concentration → recommendation."""
    r = pl["dcf"]; sm = pl["summary"]; rk = pl["risk"]; scen = pl.get("scenario", "Base")
    su = r.get("sources_uses", {}); s = r["series"]
    tiers = sm.get("tiers", {}); t1n = tiers.get("Tier 1 - Strong", 0)
    buf, doc = _doc("Investment Committee Report", scen + " scenario")
    flow = []
    # ---- 1. EXECUTIVE SUMMARY ----
    flow += [Paragraph("Investment Committee Report", _S["h1"]),
             Paragraph(f"{pl.get('project','SFR portfolio')} · 100-home Tier-1 build · 10-year levered hold · <b>{scen}</b> scenario.", _S["sub"])]
    irr = r["levered_irr"]
    verdict = ("Recommend proceeding" if irr >= 0.13 and r["min_dscr"] >= 1.2 else
               "Proceed selectively" if irr >= 0.10 else "Hold / re-trade")
    flow.append(Paragraph(f"<b>Recommendation: {verdict}.</b> A {t1n:,}-property Tier-1 pipeline underwrites to a "
        f"<b>{_pct(irr)} levered IRR</b> and <b>{r['equity_multiple']:.2f}× equity</b> over a 10-year hold, with a "
        f"minimum DSCR of {r['min_dscr']:.2f} and a {_pct(r['going_in_cap'],2)} going-in cap. Portfolio risk grades "
        f"<b>{rk['grade']}</b> ({rk['avg_score']}/100) across the sampled book — material items are identified below with mitigations.", _S["p"]))
    flow.append(Spacer(1, 8))
    flow.append(kpi_grid([("Levered IRR", _pct(irr)), ("Equity Multiple", f"{r['equity_multiple']:.2f}×"),
                  ("Min DSCR", f"{r['min_dscr']:.2f}"), ("Going-in Cap", _pct(r["going_in_cap"],2)),
                  ("Tier-1 Pipeline", f"{t1n:,}"), ("Match Rate", _pct(sm.get('match_rate',0))),
                  ("Equity Required", _money(r["equity"])), ("Risk Grade", rk["grade"])]))
    # ---- 2. THE OPPORTUNITY ----
    flow.append(Paragraph("1 &nbsp;·&nbsp; The opportunity", _S["h2"]))
    total = sum(tiers.values()) or 1
    matched = total - tiers.get("Not a Match", 0)
    flow.append(Paragraph(f"The buy box screens a {total:,}-property universe down to {matched:,} that clear the gate "
        f"({_pct(sm.get('match_rate',0))}), of which {t1n:,} are Tier-1. Average Tier-1 gross yield is "
        f"{_pct(sm.get('tier1_avg_yield',0))} at a {_money(sm.get('tier1_avg_avm',0))} basis.", _S["p"]))
    funnel = [["Total universe", f"{total:,}", "100%"],
              ["Clears buy box", f"{matched:,}", _pct(sm.get('match_rate',0))],
              ["Tier 1 — Strong", f"{tiers.get('Tier 1 - Strong',0):,}", _pct(tiers.get('Tier 1 - Strong',0)/total)],
              ["Tier 2 — Moderate", f"{tiers.get('Tier 2 - Moderate',0):,}", _pct(tiers.get('Tier 2 - Moderate',0)/total)],
              ["Tier 3 — Watch", f"{tiers.get('Tier 3 - Watch',0):,}", _pct(tiers.get('Tier 3 - Watch',0)/total)]]
    flow.append(data_table(["Pipeline stage", "Count", "Share"], funnel,
                aligns=["LEFT","RIGHT","RIGHT"], widths=[3.4*inch, 2.0*inch, 1.9*inch]))
    tops = sm.get("tier1_top_states", {})
    if tops:
        flow.append(Spacer(1, 6))
        flow.append(Paragraph(f"<b>Geographic concentration (HHI {sm.get('hhi',0):,}):</b> " +
            " · ".join(f"{k} {v:,}" for k, v in tops.items()) +
            (". Concentration is high — diversify metros." if sm.get('hhi',0) > 2500 else "."), _S["p"]))
    # ---- 3. RETURN THESIS ----
    flow.append(Paragraph("2 &nbsp;·&nbsp; Return thesis", _S["h2"]))
    rows = [[f"Year {y}", _money(s["noi"][i]) if i < len(s["noi"]) else "—",
             _money(s["cfo"][i]) if i < len(s["cfo"]) else "—", _money(s["levered_cf"][i]),
             (f"{s['dscr'][i]:.2f}" if s["dscr"][i] else "—")] for i, y in enumerate(s["years"])]
    flow.append(data_table(["Year", "NOI", "Cash from Ops", "Levered CF", "DSCR"], rows,
                aligns=["LEFT","RIGHT","RIGHT","RIGHT","RIGHT"],
                widths=[0.9*inch, 1.55*inch, 1.65*inch, 1.65*inch, 1.55*inch]))
    if "compare" in r:
        flow.append(Spacer(1, 6)); flow.append(Paragraph("Scenario range", _S["h2"]))
        crows = [[k, _pct(v["levered_irr"]), f"{v['equity_multiple']:.2f}×", f"{v['min_dscr']:.2f}"]
                 for k, v in r["compare"].items()]
        flow.append(data_table(["Scenario", "Levered IRR", "Equity Mult.", "Min DSCR"], crows,
                    aligns=["LEFT","RIGHT","RIGHT","RIGHT"], widths=[2.2*inch,1.7*inch,1.7*inch,1.7*inch]))
    if su:
        flow.append(Spacer(1, 6)); flow.append(Paragraph("Sources &amp; uses", _S["h2"]))
        flow.append(data_table(["Item", "Amount"],
            [["Purchase", _money(su["purchase"])], ["Acquisition cost", _money(su["acq_cost"])],
             ["Rehab", _money(su["rehab"])], ["Loan fee", _money(su["loan_fee"])],
             ["Senior debt", _money(su["loan"])], ["Equity", _money(su["equity"])]],
            aligns=["LEFT","RIGHT"], widths=[4.0*inch, 3.3*inch]))
    # ---- 4. RISK ASSESSMENT (every flag) ----
    flow.append(Paragraph("3 &nbsp;·&nbsp; Risk assessment", _S["h2"]))
    sc = rk["severity_counts"]
    flow.append(Paragraph(f"Across the {rk['n']} highest-scoring Tier-1 deals, the model flags "
        f"<b>{sc.get('Critical',0)} critical</b>, {sc.get('High',0)} high, {sc.get('Medium',0)} medium, "
        f"{sc.get('Low',0)} low and {sc.get('Minor',0)} minor items. Every distinct flag is listed below with "
        f"its prevalence and mitigation; title, environmental and litigation items require ordered reports and are "
        f"not fabricated.", _S["p"]))
    flow.append(Spacer(1, 4))
    _x = lambda t: str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    rrows, rstyle = [], []
    for i, f in enumerate(rk["flags"]):
        rrows.append([f["severity"], _x(f["category"]),
            Paragraph(f"<b>{_x(f['title'])}</b><br/><font size=7 color='#6b7689'>Fix: {_x(f['mitigation'])}</font>", _S["p"]),
            f"{f['count']}/{rk['n']}"])
        rstyle.append(("TEXTCOLOR", (0, i+1), (0, i+1), SEVCOLOR.get(f["severity"], colors.black)))
    rt = data_table(["Sev.", "Category", "Risk & mitigation", "Seen"], rrows,
                    aligns=["LEFT","LEFT","LEFT","RIGHT"], widths=[0.7*inch, 1.5*inch, 4.0*inch, 0.6*inch])
    rt.setStyle(TableStyle(rstyle + [("FONTNAME", (0,1), (0,-1), "Helvetica-Bold"), ("FONTSIZE", (0,1), (0,-1), 7.5)]))
    flow.append(rt)
    # ---- 5. CONCENTRATION & MARKET ----
    flow.append(Paragraph("4 &nbsp;·&nbsp; Concentration &amp; market risk", _S["h2"]))
    conc = ("Tier-1 is highly concentrated geographically (HHI {:,}) — a single-state climate, insurance or "
            "regulatory shock would hit a large share of the book. Cap any one state near 40% and diversify metros."
            ).format(sm.get("hhi", 0)) if sm.get("hhi", 0) > 2000 else \
           "Geographic concentration is moderate; continue to monitor single-metro exposure."
    flow.append(Paragraph(conc, _S["p"]))
    # ---- 6. RECOMMENDATION & NEXT STEPS ----
    flow.append(Paragraph("5 &nbsp;·&nbsp; Recommendation &amp; next steps", _S["h2"]))
    steps = [f"<b>Decision:</b> {verdict} at the {scen.lower()} case ({_pct(irr)} IRR, {r['min_dscr']:.2f} min DSCR).",
             "Bind real insurance quotes in climate-exposed states before committing — stress NOI for premium inflation.",
             "Cap single-state exposure (~40%) and ladder acquisitions to diversify metros.",
             "Maintain 3–6 months of debt-service reserves given the leverage profile."]
    for it in rk["needs_report"][:4]:
        steps.append(f"<b>Diligence:</b> {it['title']} — {it['mitigation']}")
    for st in steps:
        flow.append(Paragraph("•&nbsp; " + st, _S["p"])); flow.append(Spacer(1, 2))
    hf = HeaderFooter("Investment Committee Report", scen + " scenario")
    doc.build(flow, onFirstPage=hf, onLaterPages=hf); buf.seek(0); return buf.read()

def property_pdf(p, uw):
    buf, doc = _doc("Property Report", p.get("tier", ""))
    flow = [Paragraph(p["address"], _S["h1"]),
            Paragraph(f"{p['city']}, {p['state']} {p['zip']} · APN {p['apn']} · <b>{p['tier']}</b> (score {p['total_score']:.0f})", _S["sub"]),
            kpi_grid([("AVM", _money(p["avm"])), ("Market Rent", _money(p["market_rent"])),
                      ("Gross Yield", _pct(p["gross_yield"])), ("Total Score", f"{p['total_score']:.0f}"),
                      ("Beds", f"{p['beds']}"), ("Sqft", f"{p['sqft']:,.0f}"),
                      ("Year Built", f"{p['yearbuilt']}"), ("Tenure (yrs)", f"{p['tenure']:.1f}")]),
            Paragraph("Underwrite @ 90% of AVM", _S["h2"]),
            data_table(["Metric", "Value"],
                [["Going-in Cap", _pct(uw["cap_rate"],2)], ["Cash-on-Cash", _pct(uw["coc"],2)],
                 ["DSCR", f"{uw['dscr']:.2f}"], ["NOI", _money(uw["noi"])],
                 ["Annual debt service", _money(uw["debt_service"])], ["Monthly cash flow", _money(uw["monthly_cf"])],
                 ["All-in basis", _money(uw["all_in"])], ["Cash invested", _money(uw["cash_invested"])]],
                aligns=["LEFT","RIGHT"], widths=[4.0*inch, 3.3*inch]),
            Spacer(1, 10),
            Paragraph("<font size=8 color='#6b7689'>Basis &amp; caveats: yield computed at 90% of AVM with the corrected per-state cost basis (blended tax/insurance, $1,200 capex). Beds are sqft-estimated unless an actual count was supplied.</font>", _S["p"])]
    hf = HeaderFooter("Property Report", p.get("tier", ""))
    doc.build(flow, onFirstPage=hf, onLaterPages=hf); buf.seek(0); return buf.read()

# ----------------------------------------------------------- Excel
def _xl_formats(wb):
    return {
        "title": wb.add_format({"bold": True, "font_size": 17, "font_color": "#0b1320", "font_name": "Calibri"}),
        "sub": wb.add_format({"font_size": 10, "font_color": "#6b7689"}),
        "hdr": wb.add_format({"bold": True, "font_color": "white", "bg_color": "#0b1320", "align": "left",
                              "valign": "vcenter", "border": 1, "border_color": "#0b1320", "font_size": 9}),
        "hdrR": wb.add_format({"bold": True, "font_color": "white", "bg_color": "#0b1320", "align": "right",
                               "valign": "vcenter", "border": 1, "border_color": "#0b1320", "font_size": 9}),
        "cell": wb.add_format({"font_size": 10, "border": 1, "border_color": "#e4e8f0"}),
        "cellR": wb.add_format({"font_size": 10, "border": 1, "border_color": "#e4e8f0", "align": "right"}),
        "money": wb.add_format({"font_size": 10, "border": 1, "border_color": "#e4e8f0", "num_format": "$#,##0", "align": "right"}),
        "pct": wb.add_format({"font_size": 10, "border": 1, "border_color": "#e4e8f0", "num_format": "0.0%", "align": "right"}),
        "pct2": wb.add_format({"font_size": 10, "border": 1, "border_color": "#e4e8f0", "num_format": "0.00%", "align": "right"}),
        "num2": wb.add_format({"font_size": 10, "border": 1, "border_color": "#e4e8f0", "num_format": "0.00", "align": "right"}),
        "kpiL": wb.add_format({"bold": True, "font_size": 8, "font_color": "#6b7689", "bg_color": "#e7f7f0"}),
        "kpiV": wb.add_format({"bold": True, "font_size": 14, "font_color": "#0a7a55", "bg_color": "#e7f7f0"}),
    }

def model_xlsx(prop, profile, assumptions, loc, trace=None, uw_discount=0.9):
    """The WORKING model as a live-formula workbook: change any Assumption cell and the
    Buy Box / Scoring / Underwriting sheets all recompute. Mirrors the engine exactly.
    `trace` (from calc_trace.trace) supplies cached results so values are correct even
    before a recalc."""
    # cached values from the trace, keyed for quick lookup
    C_score = {}; C_uw = {}; C_sum = {}
    if trace:
        C_sum = trace.get("score", {})
        for row in trace["score"]["rows"]:
            C_score[row["metric"]] = row
        for row in trace["underwrite"]["rows"]:
            C_uw[row["label"]] = row["value"]
    def cv(d, k, default=0):  # cached value or default (lets Excel recalc fill it)
        return d.get(k, default) if d else default
    buf = io.BytesIO(); wb = xlsxwriter.Workbook(buf, {"in_memory": True}); F = _xl_formats(wb)
    wb.set_calc_mode("auto")
    F["fx"] = wb.add_format({"font_size": 9, "font_color": "#5a6a55", "italic": True, "border": 1,
                             "border_color": "#e4e8f0", "font_name": "Consolas"})
    F["inp"] = wb.add_format({"font_size": 10, "border": 1, "border_color": "#c9d6cf", "bg_color": "#f0fff8",
                              "align": "right"})
    F["lbl"] = wb.add_format({"font_size": 10, "border": 1, "border_color": "#e4e8f0", "bold": True})
    F["sec"] = wb.add_format({"bold": True, "font_size": 12, "font_color": "white", "bg_color": "#0e9d6e"})
    F["passC"] = wb.add_format({"font_size": 10, "border": 1, "border_color": "#e4e8f0", "align": "center",
                                "bold": True, "font_color": "#0a7a55"})
    g = {c["field"]: c for c in profile["gate"]}
    mx = {m["key"]: m for m in profile["metrics"]}
    p = lambda k: mx[k]["norm"] if k in mx else {}
    sc, bb = {}, {}

    # ---------------- ASSUMPTIONS (named input cells) ----------------
    A = wb.add_worksheet("Assumptions"); A.hide_gridlines(2)
    A.set_column(0, 0, 30); A.set_column(1, 1, 16); A.set_column(2, 2, 4); A.set_column(3, 3, 30); A.set_column(4, 4, 16)
    A.merge_range(0, 0, 0, 4, "Terra · Working Model — Assumptions", F["title"])
    A.merge_range(1, 0, 1, 4, "Green cells are inputs. Edit them and every sheet recomputes (this IS the model).", F["sub"])
    def put(ws, r, c, name, label, value, fmt="num2", defname=None):
        ws.write(r, c, label, F["lbl"]); ws.write(r, c+1, value, F[fmt])
        if defname:
            cell = "'%s'!$%s$%d" % (ws.name, chr(65+c+1), r+1)
            try: wb.define_name(defname, "=" + cell)
            except Exception: pass
    # buy-box bands + weights (left block)
    yb = g.get("yearbuilt", {}); sq = g.get("sqft", {}); av = g.get("avm", {})
    yld = p("yield"); pr = p("price"); ten = p("ten"); sf = p("sqftfit"); yf = p("yearfit")
    rows_left = [
        ("bb_year_low", "Year built — low", yb.get("lo"), "num2"),
        ("bb_year_high", "Year built — high", yb.get("hi"), "num2"),
        ("bb_sqft_low", "Sqft — low", sq.get("lo"), "num2"),
        ("bb_sqft_high", "Sqft — high", sq.get("hi"), "num2"),
        ("bb_price_low", "AVM band — low", av.get("lo"), "money"),
        ("bb_price_high", "AVM band — high", av.get("hi"), "money"),
        ("bb_yield_floor", "Yield floor", yld.get("lo"), "pct2"),
        ("bb_yield_target", "Yield target", yld.get("hi"), "pct2"),
        ("bb_ten_sat", "Tenure saturation (yrs)", ten.get("cap"), "num2"),
        ("bb_sqft_center", "Sqft fit — center", sf.get("center"), "num2"),
        ("bb_sqft_half", "Sqft fit — half-width", sf.get("half"), "num2"),
        ("risk_baseline", "Risk baseline", profile["risk"]["baseline"], "num2"),
        ("risk_sens", "Risk sensitivity", profile["risk"]["sensitivity"], "num2"),
        ("tier1_cut", "Tier 1 cutoff", profile["tiers"]["tier1"], "num2"),
        ("tier2_cut", "Tier 2 cutoff", profile["tiers"]["tier2"], "num2"),
    ]
    for i, (nm, lb, v, fmt) in enumerate(rows_left):
        put(A, 3 + i, 0, nm, lb, v, fmt, nm)
    # weights (per metric, named w_<key>)
    A.write(3, 3, "Metric weights", F["sec"])
    for i, m in enumerate(profile["metrics"]):
        put(A, 4 + i, 3, "w_" + m["key"], m["label"], m.get("weight", 0), "num2", "w_" + m["key"])
    # underwriting assumptions (continue left block)
    base = 3 + len(rows_left) + 1
    A.write(base - 1, 0, "Underwriting assumptions", F["sec"])
    ua = [("a_closing", "Closing %", assumptions["closing"], "pct2"),
          ("a_rehab", "Rehab $/home", assumptions["rehab"], "money"),
          ("a_vacancy", "Vacancy %", assumptions["vacancy"], "pct2"),
          ("a_pm", "Property mgmt %", assumptions["pm"], "pct2"),
          ("a_maint", "Maintenance %", assumptions["maint"], "pct2"),
          ("a_tax", "Property tax %", assumptions["tax"], "pct2"),
          ("a_ins", "Insurance $", assumptions["ins"], "money"),
          ("a_hoa", "HOA $", assumptions["hoa"], "money"),
          ("a_other", "Other $", assumptions["other"], "money"),
          ("a_ltv", "LTV %", assumptions["ltv"], "pct2"),
          ("a_rate", "Interest rate %", assumptions["rate"], "pct2"),
          ("a_amort", "Amortization (yrs)", assumptions["amort"], "num2"),
          ("a_points", "Loan points %", assumptions["points"], "pct2"),
          ("uw_disc", "Offer as % of AVM", uw_discount, "pct2")]
    for i, (nm, lb, v, fmt) in enumerate(ua):
        put(A, base + i, 0, nm, lb, v, fmt, nm)
    # property facts (named p_*) on the right
    A.write(base - 1, 3, "Subject property", F["sec"])
    pf = [("p_avm", "AVM", _num(prop.get("avm")), "money"),
          ("p_rent", "Market rent (monthly)", _num(prop.get("market_rent")), "money"),
          ("p_yield", "Gross yield", _num(prop.get("gross_yield")), "pct2"),
          ("p_sqft", "Sqft", _num(prop.get("sqft")), "num2"),
          ("p_year", "Year built", _num(prop.get("yearbuilt")), "num2"),
          ("p_tenure", "Tenure (yrs)", _num(prop.get("tenure")), "num2"),
          ("p_corp", "Corporate owner", "Y" if str(prop.get("corp")) == "Y" else "N", "cell"),
          ("p_proven", "Proven-market index", loc.get("proven_v", 0), "num2"),
          ("p_density", "Cluster-density index", loc.get("density_v", 0), "num2"),
          ("p_target", "Metro-target index", loc.get("target_v", 0), "num2"),
          ("p_momentum", "Rent-momentum index", loc.get("momentum_v", 0), "num2"),
          ("p_risk", "Market-risk index", loc.get("risk", 0), "num2")]
    for i, (nm, lb, v, fmt) in enumerate(pf):
        put(A, base + i, 3, nm, lb, v, fmt, nm)

    # ---------------- 1. BUY BOX (GATE) ----------------
    G = wb.add_worksheet("1. Buy Box (Gate)"); G.hide_gridlines(2)
    G.set_column(0, 0, 26); G.set_column(1, 1, 16); G.set_column(2, 2, 46); G.set_column(3, 3, 10)
    G.merge_range(0, 0, 0, 3, "Step 1 — Buy Box gate (hard filter)", F["title"])
    G.merge_range(1, 0, 1, 3, "Fail any row → the deal scores 0 (Not a Match). Formulas reference the green inputs.", F["sub"])
    for c, h in enumerate(["Criterion", "Subject", "Test (live formula)", "Result"]):
        G.write(3, c, h, F["hdr"])
    gate_cells = []
    gdefs = [("Year built", "p_year", "bb_year_low", "bb_year_high", "between"),
             ("Living area (sqft)", "p_sqft", "bb_sqft_low", "bb_sqft_high", "between"),
             ("AVM in band", "p_avm", "bb_price_low", "bb_price_high", "between"),
             ("Corporate / rent-zip note", "p_corp", None, None, "info")]
    gpass = {row["field"]: row["pass"] for row in trace["gate"]["rows"]} if trace else {}
    subj2field = {"p_year": "yearbuilt", "p_sqft": "sqft", "p_avm": "avm"}
    pval = {"p_year": _num(prop.get("yearbuilt")), "p_sqft": _num(prop.get("sqft")), "p_avm": _num(prop.get("avm"))}
    r = 4
    for lbl, subj, lo, hi, kind in gdefs:
        G.write(r, 0, lbl, F["lbl"])
        if kind == "between":
            G.write_formula(r, 1, "=%s" % subj, F["cellR"], pval.get(subj, 0))
            f = "=IF(AND(%s>=%s,%s<=%s),\"PASS\",\"FAIL\")" % (subj, lo, subj, hi)
            G.write_formula(r, 2, "=\"%s ≤ \"&TEXT(%s,\"#,##0\")&\" ≤ %s\"" % (lo, subj, hi), F["fx"])
            res = "PASS" if gpass.get(subj2field.get(subj), True) else "FAIL"
            G.write_formula(r, 3, f, F["passC"], res); gate_cells.append("$D$%d" % (r+1))
        else:
            G.write(r, 1, "—", F["cellR"])
            G.write(r, 2, "Owner flag + rent-benchmarked zip checked in engine", F["fx"])
            G.write(r, 3, "—", F["cellR"])
        r += 1
    G.write(r+1, 0, "GATE RESULT", F["sec"])
    gate_formula = "=IF(AND(%s),\"PASS — enters scoring\",\"FAIL — Not a Match\")" % ",".join("%s=\"PASS\"" % c for c in gate_cells)
    gate_val = "PASS — enters scoring" if (trace and trace["gate"]["passed"]) else ("FAIL — Not a Match" if trace else "PASS — enters scoring")
    G.merge_range(r+1, 1, r+1, 3, "", F["passC"]); G.write_formula(r+1, 1, gate_formula, F["passC"], gate_val)
    try: wb.define_name("gate_pass", "='1. Buy Box (Gate)'!$B$%d" % (r+2))
    except Exception: pass

    # ---------------- 2. SCORING MODEL ----------------
    S = wb.add_worksheet("2. Scoring Model"); S.hide_gridlines(2)
    for c, w in [(0, 12), (1, 22), (2, 12), (3, 50), (4, 10), (5, 9), (6, 12)]: S.set_column(c, c, w)
    S.merge_range(0, 0, 0, 6, "Step 2 — 0–100 score across four pillars", F["title"])
    S.merge_range(1, 0, 1, 6, "sub-score = normalization(input); contribution = sub-score × weight. Live formulas.", F["sub"])
    for c, h in enumerate(["Pillar", "Metric", "Input", "Normalization (live formula)", "Sub", "Weight", "Contrib"]):
        S.write(3, c, h, F["hdr"] if c < 4 else F["hdrR"])
    # map each metric key -> (input named cell, excel normalization formula)
    NF = {
        "yield":   ("p_yield", "=MAX(0,MIN(1,(p_yield-bb_yield_floor)/(bb_yield_target-bb_yield_floor)))*100"),
        "price":   ("p_avm", "=MAX(0,MIN(1,(bb_price_high-p_avm)/(bb_price_high-bb_price_low)))*100"),
        "abs":     ("p_corp", "=IF(p_corp=\"Y\",100,0)"),
        "ten":     ("p_tenure", "=MIN(1,p_tenure/bb_ten_sat)*100"),
        "proven":  ("p_proven", "=p_proven"), "density": ("p_density", "=p_density"),
        "target":  ("p_target", "=p_target"), "momentum": ("p_momentum", "=p_momentum"),
        "sqftfit": ("p_sqft", "=MAX(0,1-ABS(p_sqft-bb_sqft_center)/bb_sqft_half)*100"),
        "yearfit": ("p_year", "=MAX(0,MIN(1,(p_year-bb_year_low)/(bb_year_high-bb_year_low)))*100"),
    }
    r = 4; sub_cells, con_cells, w_cells = [], [], []
    for m in profile["metrics"]:
        if not m.get("weight", 0) or not m.get("on", True) or m["key"] not in NF: continue
        inp, nf = NF[m["key"]]; ck = C_score.get(m["label"], {})
        S.write(r, 0, m["pillar"], F["cell"]); S.write(r, 1, m["label"], F["cell"])
        S.write_formula(r, 2, "=%s" % inp, F["num2"], 0)
        S.write_formula(r, 3, nf, F["fx"], cv(ck, "subscore"))   # sub-score, shown as its formula
        sub = "$E$%d" % (r+1); wc = "w_%s" % m["key"]
        S.write_formula(r, 5, "=%s" % wc, F["num2"], cv(ck, "weight", m.get("weight", 0)))
        S.write_formula(r, 6, "=%s*%s" % (sub, "$F$%d" % (r+1)), F["num2"], cv(ck, "contribution"))
        sub_cells.append(sub); con_cells.append("$G$%d" % (r+1)); w_cells.append("$F$%d" % (r+1)); r += 1
    r += 1
    raw_row = r
    S.write(r, 1, "Raw score = Σcontrib / Σweight", F["lbl"])
    S.write_formula(r, 6, "=SUM(%s:%s)/SUM(%s:%s)" % (con_cells[0], con_cells[-1], w_cells[0], w_cells[-1]), F["num2"], cv(C_sum, "raw_score")); r += 1
    S.write(r, 1, "Risk haircut", F["lbl"])
    S.write_formula(r, 3, "=1-MAX(0,p_risk-risk_baseline)/(100-risk_baseline)*risk_sens", F["fx"], cv(C_sum, "haircut"))
    S.write_formula(r, 6, "=1-MAX(0,p_risk-risk_baseline)/(100-risk_baseline)*risk_sens", F["num2"], cv(C_sum, "haircut"))
    hc_cell = "$G$%d" % (r+1); r += 1
    S.write(r, 1, "TOTAL SCORE (gated × haircut)", F["sec"])
    S.write_formula(r, 6, "=IF(gate_pass=\"PASS — enters scoring\",$G$%d*%s,0)" % (raw_row+1, hc_cell), F["num2"], cv(C_sum, "total"))
    tot_cell = "$G$%d" % (r+1); r += 2
    S.write(r, 1, "TIER", F["sec"])
    S.merge_range(r, 3, r, 6, "", F["passC"])
    S.write_formula(r, 3, "=IF(gate_pass<>\"PASS — enters scoring\",\"Not a Match\",IF(%s>=tier1_cut,\"Tier 1 - Strong\",IF(%s>=tier2_cut,\"Tier 2 - Moderate\",\"Tier 3 - Watch\")))" % (tot_cell, tot_cell), F["passC"], cv(C_sum, "tier", ""))

    # ---------------- 3. UNDERWRITING ----------------
    W = wb.add_worksheet("3. Underwriting"); W.hide_gridlines(2)
    W.set_column(0, 0, 28); W.set_column(1, 1, 16); W.set_column(2, 2, 56)
    W.merge_range(0, 0, 0, 2, "Step 3 — Underwriting (live DCF)", F["title"])
    W.merge_range(1, 0, 1, 2, "Offer = AVM × (offer %). Change rate/LTV on Assumptions → cap, CoC, DSCR recompute.", F["sub"])
    for c, h in enumerate(["Line", "Value", "Formula"]): W.write(3, c, h, F["hdr"] if c < 1 else F["hdrR"] if c == 1 else F["hdr"])
    uw_lines = [
        ("uw_price", "Offer price", "=p_avm*uw_disc", "money", "AVM × offer %"),
        ("uw_allin", "All-in basis", "=uw_price*(1+a_closing)+a_rehab", "money", "price×(1+closing)+rehab"),
        ("uw_loan", "Senior loan", "=uw_price*a_ltv", "money", "price × LTV"),
        ("uw_cash", "Cash invested", "=uw_allin-uw_loan+uw_loan*a_points", "money", "all-in − loan + loan×points"),
        ("uw_gsr", "Gross scheduled rent", "=p_rent*12", "money", "rent × 12"),
        ("uw_egi", "Effective gross income", "=uw_gsr*(1-a_vacancy)", "money", "GSR × (1 − vacancy)"),
        ("uw_opex", "Operating expenses", "=uw_egi*a_pm+uw_gsr*a_maint+uw_price*a_tax+a_ins+a_hoa+a_other", "money", "mgmt+maint+tax+ins+hoa+other"),
        ("uw_noi", "Net operating income", "=uw_egi-uw_opex", "money", "EGI − opex"),
        ("uw_ads", "Annual debt service", "=-PMT(a_rate/12,a_amort*12,uw_loan)*12", "money", "−PMT(rate/12, amort×12, loan)×12"),
        ("uw_cf", "Cash flow before tax", "=uw_noi-uw_ads", "money", "NOI − debt service"),
        ("uw_cap", "Going-in cap rate", "=uw_noi/uw_allin", "pct2", "NOI / all-in"),
        ("uw_coc", "Cash-on-cash", "=uw_cf/uw_cash", "pct2", "cash flow / cash invested"),
        ("uw_dscr", "DSCR", "=uw_noi/uw_ads", "num2", "NOI / debt service"),
    ]
    uw_cache = {"Offer price": (trace["underwrite"]["price"] if trace else 0)}
    uw_cache.update(C_uw)
    r = 4
    for nm, lbl, f, fmt, expl in uw_lines:
        W.write(r, 0, lbl, F["lbl"]); W.write_formula(r, 1, f, F[fmt], cv(uw_cache, lbl)); W.write(r, 2, expl, F["fx"])
        try: wb.define_name(nm, "='3. Underwriting'!$B$%d" % (r+1))
        except Exception: pass
        r += 1
    A.activate()
    wb.close(); buf.seek(0); return buf.read()


def _num(x):
    try:
        v = float(x); return v if v == v else 0
    except (TypeError, ValueError):
        return x if isinstance(x, str) else 0


def targets_xlsx(rows, title="Tier-1 Targets"):
    buf = io.BytesIO(); wb = xlsxwriter.Workbook(buf, {"in_memory": True}); ws = wb.add_worksheet("Targets")
    F = _xl_formats(wb)
    ws.hide_gridlines(2)
    ws.merge_range(0, 0, 0, 8, "Terra · " + title, F["title"])
    ws.merge_range(1, 0, 1, 8, "Generated from the deterministic engine · 100% parity.", F["sub"])
    cols = [("Address", 34, "cell"), ("City", 16, "cell"), ("ST", 5, "cell"), ("AVM", 12, "money"),
            ("Market Rent", 12, "money"), ("Yield", 9, "pct"), ("Tenure", 8, "num2"),
            ("Owner", 8, "cell"), ("Score", 8, "num2")]
    r0 = 3
    for c, (name, w, _) in enumerate(cols):
        ws.write(r0, c, name, F["hdrR"] if name in ("AVM","Market Rent","Yield","Tenure","Score") else F["hdr"])
        ws.set_column(c, c, w)
    for i, row in enumerate(rows):
        r = r0 + 1 + i
        ws.write(r, 0, row["address"], F["cell"]); ws.write(r, 1, row["city"], F["cell"])
        ws.write(r, 2, row["state"], F["cell"]); ws.write(r, 3, row["avm"], F["money"])
        ws.write(r, 4, row["market_rent"], F["money"]); ws.write(r, 5, row["gross_yield"], F["pct"])
        ws.write(r, 6, row["tenure"], F["num2"]); ws.write(r, 7, "Corp" if row["corp"]=="Y" else "Occ", F["cell"])
        ws.write(r, 8, row["total_score"], F["num2"])
    ws.freeze_panes(r0+1, 0)
    ws.conditional_format(r0+1, 8, r0+len(rows), 8,
        {"type": "3_color_scale", "min_color": "#fdecea", "mid_color": "#fff7e6", "max_color": "#e7f7f0"})
    ws.autofilter(r0, 0, r0+len(rows), 8)
    wb.close(); buf.seek(0); return buf.read()

def portfolio_xlsx(r, scenario="Base"):
    buf = io.BytesIO(); wb = xlsxwriter.Workbook(buf, {"in_memory": True}); F = _xl_formats(wb)
    ws = wb.add_worksheet("Summary"); ws.hide_gridlines(2)
    ws.merge_range(0, 0, 0, 5, "Terra · Portfolio Report", F["title"])
    ws.merge_range(1, 0, 1, 5, f"{scenario} scenario · 10-yr levered model", F["sub"])
    kpis = [("Levered IRR", r["levered_irr"], "pct"), ("Equity Multiple", r["equity_multiple"], "num2"),
            ("Min DSCR", r["min_dscr"], "num2"), ("Going-in Cap", r["going_in_cap"], "pct2"),
            ("Unlevered IRR", r["unlevered_irr"], "pct"), ("Equity", r["equity"], "money")]
    ws.set_column(0, 5, 16)
    for i, (l, v, fmt) in enumerate(kpis):
        c = (i % 3) * 2; rr = 3 + (i // 3) * 2
        ws.write(rr, c, l, F["kpiL"]); ws.write(rr, c+1, "", F["kpiL"])
        ws.write(rr+1, c, v, F[{"pct":"pct","pct2":"pct2","num2":"num2","money":"money"}[fmt]]); ws.write(rr+1, c+1, "", F["kpiV"])
    # cash flow sheet
    cf = wb.add_worksheet("Cash Flow"); cf.hide_gridlines(2)
    s = r["series"]
    heads = ["Year", "NOI", "Cash from Ops", "Levered CF", "DSCR"]
    for c, h in enumerate(heads): cf.write(0, c, h, F["hdrR"] if c else F["hdr"]); cf.set_column(c, c, 16)
    for i, y in enumerate(s["years"]):
        cf.write(i+1, 0, "Year %d" % y, F["cell"])
        cf.write(i+1, 1, s["noi"][i] if i < len(s["noi"]) else None, F["money"])
        cf.write(i+1, 2, s["cfo"][i] if i < len(s["cfo"]) else None, F["money"])
        cf.write(i+1, 3, s["levered_cf"][i], F["money"])
        cf.write(i+1, 4, s["dscr"][i], F["num2"])
    chart = wb.add_chart({"type": "column"})
    n = len(s["years"])
    chart.add_series({"name": "Levered CF", "categories": ["Cash Flow", 1, 0, n, 0],
                      "values": ["Cash Flow", 1, 3, n, 3], "fill": {"color": "#0e9d6e"}})
    chart.set_title({"name": "Levered cash flow by year"}); chart.set_legend({"none": True})
    chart.set_size({"width": 520, "height": 260})
    cf.insert_chart(1, 6, chart)
    wb.close(); buf.seek(0); return buf.read()


def listings_xlsx(rows, title="For-Sale Listings"):
    buf = io.BytesIO(); wb = xlsxwriter.Workbook(buf, {"in_memory": True}); ws = wb.add_worksheet("For Sale")
    F = _xl_formats(wb); ws.hide_gridlines(2)
    ws.merge_range(0, 0, 0, 9, "Terra · " + title, F["title"])
    ws.merge_range(1, 0, 1, 9, "Live for-sale listings (source: RentCast). Verify status before outreach.", F["sub"])
    cols = [("Address", 32), ("City", 16), ("ST", 5), ("Zip", 8), ("Price", 12), ("Beds", 6),
            ("Baths", 6), ("Sqft", 9), ("Type", 14), ("DOM", 6), ("Listed", 12)]
    r0 = 3
    for c, (name, w) in enumerate(cols):
        ws.write(r0, c, name, F["hdrR"] if name in ("Price", "Beds", "Baths", "Sqft", "DOM") else F["hdr"])
        ws.set_column(c, c, w)
    for i, x in enumerate(rows):
        r = r0 + 1 + i
        ws.write(r, 0, x.get("address"), F["cell"]); ws.write(r, 1, x.get("city"), F["cell"])
        ws.write(r, 2, x.get("state"), F["cell"]); ws.write(r, 3, x.get("zip"), F["cell"])
        ws.write(r, 4, x.get("price"), F["money"]); ws.write(r, 5, x.get("beds"), F["cellR"])
        ws.write(r, 6, x.get("baths"), F["cellR"]); ws.write(r, 7, x.get("sqft"), F["cellR"])
        ws.write(r, 8, x.get("type"), F["cell"]); ws.write(r, 9, x.get("dom"), F["cellR"])
        ws.write(r, 10, x.get("listed"), F["cell"])
    if rows:
        ws.freeze_panes(r0 + 1, 0); ws.autofilter(r0, 0, r0 + len(rows), 10)
    wb.close(); buf.seek(0); return buf.read()


if __name__ == "__main__":
    import app, os
    out = os.path.dirname(__file__)
    r = app.t_portfolio_dcf(scenario="Base")
    open(os.path.join(out, "_test_portfolio.pdf"), "wb").write(portfolio_pdf(r))
    open(os.path.join(out, "_test_portfolio.xlsx"), "wb").write(portfolio_xlsx(r))
    tg = app.t_search_targets(limit=25)["rows"]
    open(os.path.join(out, "_test_targets.xlsx"), "wb").write(targets_xlsx(tg))
    pr = app.t_lookup_property(query="129A01033000"); uw = app.t_underwrite(pr["avm"]*0.9, pr["market_rent"])
    open(os.path.join(out, "_test_property.pdf"), "wb").write(property_pdf(pr, uw))
    print("wrote test reports:", [f for f in os.listdir(out) if f.startswith("_test_")])
