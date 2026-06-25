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
