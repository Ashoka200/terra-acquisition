"""
assistant.py — ATLAS: the in-app acquisition copilot (mirrors LUMEN).
Two brains:
  * OFFLINE brain (rule-based intent router) — works with NO API key. Parses the
    question, calls the deterministic engine tools, formats a LUMEN-style answer.
  * AI brain (Anthropic tool-use loop) — used when ANTHROPIC_API_KEY is set, for
    richer free-form questions. Still grounded: the LLM calls tools, never computes.
"""
import os, re, json
import knowledge

MODEL = os.environ.get("ATLAS_MODEL", "claude-opus-4-8")

SYSTEM = """You are ATLAS, the residential-acquisition copilot for United Brothers.
You sit on a deterministic Python engine that is a 100%-parity port of the firm's Excel
acquisition model. NEVER do math yourself — always call a tool and report what it returns.

OUTPUT — clean, logically sequenced (your reply renders as GitHub-flavored markdown):
1. **Bold one-line bottom line** first — the direct answer.
2. A short, ordered explanation. Sequence it logically (cause → effect, or step → step).
3. Use a **markdown table** whenever you list 3+ items, rank options, or compare scenarios.
   Right-align the read: put the label column first, numbers after. Keep tables tight.
4. End with **Next:** one concrete action.
Keep it scannable — bullets and tables over long paragraphs. State **Confidence** (one word)
when the answer is an estimate or depends on assumptions.

DATA & STATISTICS — you CAN answer distributional questions. For "median / average /
typical / spread / distribution / percentile of <field>" call `field_stats`; for "how many
… <field> <over/under> <value>" call `count_where`. Fields: sqft, avm, market_rent,
gross_yield, yearbuilt, tenure, total_score, beds — optionally filtered by tier/state.
Report the median AND the spread (p25–p75 or p10–p90), not just one number. Never say you
"can't" compute a statistic on these fields — you have the tool.

WHAT-IF & HYPOTHETICALS — always supported. When the user asks "what if <X changes>"
(rate, LTV, rent, price, vacancy, exit cap, growth, # homes, etc.):
  • Re-run the relevant tool(s) with the adjusted inputs (underwrite / reverse_solve /
    portfolio_dcf accept rate, ltv, vacancy, pm, tax, exit_cap, rent_growth, homes, …).
  • Present a **Before → After** comparison TABLE with the deltas, then a one-line takeaway
    on whether the change helps or hurts and why. Never hand-wave a hypothetical — compute it.
  • For multi-step or compounded what-ifs, run each leg and show the cumulative effect.

ALWAYS FLAG ISSUES — never recommend a property or a metric without its risks:
  • For a specific deal, call `risk` (grade + flags) and/or `explain_calc` (score drivers,
    gate, cap/CoC/DSCR) and surface the material flags with their mitigations.
  • Call out standing caveats that apply: negative leverage (cap < debt rate), thin DSCR
    (< 1.25), sub-8% gross yield, AVM-is-a-model (not an appraisal), single-state/metro
    concentration, climate/insurance exposure (FL/LA/CA/TX/coastal), pre-1978 lead paint.
  • Be honest about data limits: title/liens, environmental (Phase I) and litigation are
    NOT in the dataset — flag them as "needs an ordered report," never fabricate them.
A recommendation with no caveats is incomplete — include the risks every time."""

TOOL_SPECS = [
    {"name": "market_summary", "description": "Tier distribution, geo concentration, HHI, match rate.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "search_targets", "description": "Filter the scored universe; returns top rows by score.",
     "input_schema": {"type": "object", "properties": {
         "tier": {"type": "string"}, "state": {"type": "string"}, "min_yield": {"type": "number"},
         "max_price": {"type": "number"}, "corp_only": {"type": "boolean"}, "limit": {"type": "integer"}}}},
    {"name": "lookup_property", "description": "Look up one property by APN or address fragment.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "underwrite", "description": "Forward underwrite: price+rent -> cap, CoC, DSCR, cash flow.",
     "input_schema": {"type": "object", "properties": {
         "price": {"type": "number"}, "monthly_rent": {"type": "number"},
         "rate": {"type": "number"}, "ltv": {"type": "number"}}, "required": ["price", "monthly_rent"]}},
    {"name": "reverse_solve", "description": "Solve the purchase price that hits a target cap/coc/dscr.",
     "input_schema": {"type": "object", "properties": {
         "target_metric": {"type": "string", "enum": ["cap", "coc", "dscr"]},
         "target": {"type": "number"}, "monthly_rent": {"type": "number"},
         "rate": {"type": "number"}, "ltv": {"type": "number"}}, "required": ["target_metric", "target", "monthly_rent"]}},
    {"name": "portfolio_dcf", "description": "10-yr portfolio DCF: levered/unlevered IRR, EMx, DSCR.",
     "input_schema": {"type": "object", "properties": {"scenario": {"type": "string"}}}},
    {"name": "geocode", "description": "Convert an address/place to lat,lon (and zip). Use before site_analysis when given an address.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "site_analysis", "description": "Site intelligence at a lat,lon: highest-and-best-use ranking across 10 uses, distance to facilities/demand generators, FEMA flood zone, risk flags.",
     "input_schema": {"type": "object", "properties": {"lat": {"type": "number"}, "lon": {"type": "number"}}, "required": ["lat", "lon"]}},
    {"name": "massing", "description": "Estimate keys/units a parcel supports from area + footprint shape + zoning (FAR/height/coverage), with vs without ground-floor retail.",
     "input_schema": {"type": "object", "properties": {"area": {"type": "number"}, "use": {"type": "string"},
        "shape": {"type": "string"}, "far": {"type": "number"}, "height": {"type": "number"}, "coverage": {"type": "number"}}, "required": ["area"]}},
    {"name": "explain_calc", "description": "Trace ONE deal end-to-end: gate pass/fail, top score drivers (metric/pillar/sub-score/weight/contribution), raw→haircut→total, tier, and cap/CoC/DSCR. Use to answer 'why is X a Tier 1/3?', 'what drives this score?', 'show the math for this property'.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "risk", "description": "Multi-dimension acquisition risk for one property: grade A–F, score, decision, and the top severity-ranked flags (flood, climate/insurance, regulatory, age, leverage, valuation) with mitigations.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "field_stats", "description": "Descriptive statistics over the scored universe for any numeric field: count, mean, MEDIAN, std, min, max, and percentiles (p10/p25/p75/p90). Use for 'what is the median/average/typical/spread/distribution of <field>'. Fields: sqft, avm, market_rent, gross_yield, yearbuilt, tenure, total_score, beds. Optional tier and state/city filter.",
     "input_schema": {"type": "object", "properties": {"field": {"type": "string"},
         "tier": {"type": "string"}, "state": {"type": "string"}}, "required": ["field"]}},
    {"name": "count_where", "description": "Count properties whose numeric field meets a condition. Use for 'how many homes built before 1980', 'how many under $200k', etc. op is one of < <= > >= == !=. Optional tier and state/city filter.",
     "input_schema": {"type": "object", "properties": {"field": {"type": "string"},
         "op": {"type": "string", "enum": ["<", "<=", ">", ">=", "==", "!="]}, "value": {"type": "number"},
         "tier": {"type": "string"}, "state": {"type": "string"}}, "required": ["field", "op", "value"]}},
]

# ----------------------------------------------------------- offline parsing
def _money(s):
    m = re.search(r"\$?\s*([\d,]+(?:\.\d+)?)\s*([kKmM])?", s)
    if not m: return None
    v = float(m.group(1).replace(",", ""))
    suf = (m.group(2) or "").lower()
    return v * (1000 if suf == "k" else 1_000_000 if suf == "m" else 1)

def _all_money(s):
    return [(_money(x[0]+x[1]) if False else (lambda v,suf: v*(1000 if suf=='k' else 1_000_000 if suf=='m' else 1))(float(x[0].replace(',','')), (x[1] or '').lower()))
            for x in re.findall(r"\$?\s*([\d,]+(?:\.\d+)?)\s*([kKmM])?", s)]

def _pct(s):
    m = re.search(r"([\d.]+)\s*%", s)
    if m: return float(m.group(1)) / 100
    m = re.search(r"\b(0?\.\d+)\b", s)
    return float(m.group(1)) if m else None

STATES = {"AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA","KS","KY",
          "LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ","NM","NY","NC","ND",
          "OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT","VA","WA","WV","WI","WY"}
STATE_NAMES = {"texas":"TX","georgia":"GA","florida":"FL","tennessee":"TN","north carolina":"NC",
               "arizona":"AZ","nevada":"NV"}

def _state(s):
    for nm, ab in STATE_NAMES.items():
        if nm in s.lower(): return ab
    for tok in re.findall(r"\b([A-Z]{2})\b", s):
        if tok in STATES: return tok
    return None

def _fmt_money(x): return "${:,.0f}".format(x)
def _fmt_pct(x, d=1): return ("{:."+str(d)+"f}%").format(x*100)
def _resp(text, blocks=None): return {"text": text, "blocks": blocks or []}
def _kpis(items): return {"type": "kpis", "items": [{"label": l, "value": v} for l, v in items]}
def _table(cols, rows): return {"type": "table", "cols": cols, "rows": rows}

def offline_answer(msg, dispatch, profile=None, assumptions=None):
    m = msg.lower()
    ap = re.search(r"\b([A-Z0-9]{6,}|\d{2,}[A-Z]\d+)\b", msg)
    has_prop_ref = bool(ap) or bool(re.search(r"\d{2,}\s+[A-Za-z]", msg))
    try:
        # 0) knowledge base — definitions / benchmarks / model concepts resolve first
        #    (guards inside answer_kb defer specific-deal & compute questions to the intents below)
        _statq = any(w in m for w in ["median", "average", "mean ", "percentile", "distribution",
                    "std", "typical", "spread", "how many", "number of", "count of", "count "])
        if profile is not None and not has_prop_ref and not _statq:
            kb = knowledge.answer_kb(msg, profile, assumptions or {}, snapshot=None)
            if kb: return _resp(kb)
        # 0.65) descriptive statistics on the universe (median/mean/percentiles + counts)
        if "field_stats" in dispatch and any(w in m for w in ["median", "average", "mean ", "percentile",
                "distribution", "std", "typical", "how many", "number of", "count of", "spread of"]):
            FIELDS = ["square feet", "square foot", "living area", "sqft", "avm", "price", "value",
                      "market rent", "rent", "gross yield", "yield", "year built", "yearbuilt", "built",
                      "age", "tenure", "total score", "score", "bedrooms", "beds"]
            fld = next((k for k in FIELDS if k in m), None)
            cntq = any(w in m for w in ["how many", "number of", "count"])
            if not fld and cntq and re.search(r"\$\s*\d", m):
                fld = "price"  # "how many under $200k" → count on AVM
            if fld:
                tier = ("Tier 1 - Strong" if "tier 1" in m or "tier-1" in m else "Tier 2 - Moderate" if "tier 2" in m
                        else "Tier 3 - Watch" if "tier 3" in m else None)
                stt = _state(msg)
                cw = re.search(r"(under|over|below|above|less than|more than|at least|at most|younger than|older than|before|after)\s*\$?\s*([\d,]+)\s*([kKmM]?)", m)
                if cntq and cw:
                    opmap = {"under": "<", "below": "<", "less than": "<", "younger than": "<", "before": "<",
                             "over": ">", "above": ">", "more than": ">", "older than": ">", "after": ">",
                             "at least": ">=", "at most": "<="}
                    val = float(cw.group(2).replace(",", "")); suf = (cw.group(3) or "").lower()
                    val *= 1000 if suf == "k" else 1_000_000 if suf == "m" else 1
                    r = dispatch["count_where"](field=fld, op=opmap[cw.group(1)], value=val, tier=tier, state=stt)
                    if not r.get("error"):
                        return _resp(f"**{r['count']:,} of {r['of']:,} ({_fmt_pct(r['share'])}) have {r['field']} {r['op']} {r['value']:,.0f}**"
                                     + (f" in {stt}" if stt else "") + (f" · {tier}" if tier else "") + ".")
                r = dispatch["field_stats"](field=fld, tier=tier, state=stt)
                if not r.get("error") and r.get("count"):
                    fld2 = r["field"]
                    fm = (lambda x: _fmt_pct(x, 1)) if fld2 == "gross_yield" else \
                         (lambda x: _fmt_money(x)) if fld2 in ("avm", "market_rent") else (lambda x: f"{x:,.0f}")
                    scope = (f" · {tier}" if tier else "") + (f" · {stt}" if stt else "")
                    tbl = [["Median", fm(r["median"])], ["Mean", fm(r["mean"])],
                           ["25th–75th pct", f"{fm(r['p25'])} – {fm(r['p75'])}"],
                           ["10th–90th pct", f"{fm(r['p10'])} – {fm(r['p90'])}"],
                           ["Min – Max", f"{fm(r['min'])} – {fm(r['max'])}"]]
                    return _resp(f"**Median {fld2.replace('_',' ')} is {fm(r['median'])}** across {r['count']:,} properties{scope}.\n"
                                 f"**Spread:** half fall between {fm(r['p25'])} and {fm(r['p75'])} (25th–75th pct).",
                                 [_table(["Statistic", "Value"], tbl)])
        # 0.7) WHAT-IF / hypothetical — vary one assumption, show Before -> After + the risk
        if any(w in m for w in ["what if", "what happens if", "if i ", "if the ", "suppose ", "scenario"]):
            # only $-prefixed or k/m-suffixed amounts (so "8%" isn't read as money)
            amts = []
            for mt in re.finditer(r"\$\s*([\d,]+(?:\.\d+)?)\s*([kKmM]?)|\b([\d,]+(?:\.\d+)?)\s*([kKmM])\b", m):
                num = mt.group(1) or mt.group(3); suf = (mt.group(2) or mt.group(4) or "").lower()
                amts.append(float(num.replace(",", "")) * (1000 if suf == "k" else 1_000_000 if suf == "m" else 1))
            price = max([a for a in amts if a > 40000], default=None) or 270000
            rmt = re.search(r"(?:rent|renting|rents?\s+(?:at|for)|@)\D{0,8}\$?\s*([\d,]+)", m)
            rent = float(rmt.group(1).replace(",", "")) if rmt else None
            if rent is not None and rent < 300:  # caught a percent like "rent drops 10%"
                rent = None
            if rent is None:
                cand = [a for a in amts if 300 <= a <= 20000 and a != price]
                rent = cand[0] if cand else None
            rent = rent or 2200
            over = {}
            rr = re.search(r"(?:rate|interest)\D{0,6}([\d.]+)\s*%", m) or re.search(r"([\d.]+)\s*%\s*(?:rate|interest)", m)
            if rr and ("rate" in m or "interest" in m): over["rate"] = float(rr.group(1)) / 100
            dn = re.search(r"([\d.]+)\s*%\s*down", m)
            lv = re.search(r"(?:ltv|leverage|loan)\D{0,6}([\d.]+)\s*%", m)
            if dn: over["ltv"] = 1 - float(dn.group(1)) / 100
            elif lv and any(w in m for w in ["ltv", "leverage", "loan"]): over["ltv"] = float(lv.group(1)) / 100
            vc = re.search(r"vacancy\D{0,6}([\d.]+)\s*%", m)
            if vc: over["vacancy"] = float(vc.group(1)) / 100
            rent2 = rent
            rc = re.search(r"rent\D{0,16}([\d.]+)\s*%", m)
            if rc:
                sign = -1 if any(w in m for w in ["drop", "fall", "down", "decrease", "lower", "cut"]) else 1
                rent2 = round(rent * (1 + sign * float(rc.group(1)) / 100))
            if over or rent2 != rent:
                base = dispatch["underwrite"](price=price, monthly_rent=rent)
                new = dispatch["underwrite"](price=price, monthly_rent=rent2, **over)
                rows = [["Cap rate", _fmt_pct(base["cap_rate"], 2), _fmt_pct(new["cap_rate"], 2)],
                        ["Cash-on-cash", _fmt_pct(base["coc"], 2), _fmt_pct(new["coc"], 2)],
                        ["DSCR", f"{base['dscr']:.2f}", f"{new['dscr']:.2f}"],
                        ["Monthly CF", _fmt_money(base["monthly_cf"]), _fmt_money(new["monthly_cf"])]]
                rate_used = over.get("rate", 0.0725)
                helps = new["dscr"] >= base["dscr"] and new["coc"] >= base["coc"]
                flags = []
                if new["cap_rate"] < rate_used: flags.append("negative leverage — cap is below the debt rate, so leverage now hurts returns")
                if new["dscr"] < 1.25: flags.append(f"DSCR {new['dscr']:.2f} is below the 1.25x lenders want")
                if new["coc"] < 0.04: flags.append("cash-on-cash under 4% leaves little cushion")
                issue = (" · ".join(flags) if flags else "coverage and yield stay within normal bounds")
                return _resp(
                    f"**What-if at {_fmt_money(price)} / {_fmt_money(rent2)} rent: the change {'helps' if helps else 'weakens'} the deal.**\n"
                    f"**Watch:** {issue}.\n"
                    f"**Next:** open Underwriting to tune any input, or ask another what-if.",
                    [_table(["Metric", "Before", "After"], rows)])
        # 1) reverse goal-seek
        if any(w in m for w in ["what price", "how much", "price to", "pay to", "price that", "implied price"]) or \
           (("price" in m or "pay" in m) and any(w in m for w in ["dscr", "cap", "cash-on-cash", "cash on cash", "coc", "yield"])):
            tm = "dscr" if "dscr" in m else "cap" if "cap" in m else "coc"
            rent = None
            rm = re.search(r"(?:rent|@)\D{0,6}\$?\s*([\d,]+)", m)
            if rm: rent = float(rm.group(1).replace(",", ""))
            tgt = (1.0 if tm == "dscr" else 0.0)
            if tm == "dscr":
                d = re.search(r"([\d.]+)\s*(?:x|dscr)", m) or re.search(r"dscr\D{0,6}([\d.]+)", m)
                tgt = float(d.group(1)) if d else 1.25
            else:
                tgt = _pct(m) or (0.07 if tm == "cap" else 0.08)
            rent = rent or 2200
            r = dispatch["reverse_solve"](target_metric=tm, target=tgt, monthly_rent=rent)
            v = r["verify"]
            return _resp(
                f"**You can pay {_fmt_money(r['implied_price'])} to hit a "
                f"{tgt if tm=='dscr' else _fmt_pct(tgt)} {tm.upper()} at {_fmt_money(rent)}/mo rent.**\n"
                f"**Confidence:** High · closed-form solve, verified.\n"
                f"**Basis:** corrected per-state cost defaults; rate/LTV adjustable in Underwriting.",
                [_kpis([("Implied price", _fmt_money(r['implied_price'])), ("Cap", _fmt_pct(v['cap_rate'],2)),
                        ("Cash-on-cash", _fmt_pct(v['coc'],2)), ("DSCR", f"{v['dscr']:.2f}")])])
        # 2) forward underwrite
        if "underwrite" in m or ("cap" in m and ("$" in m or " at " in m)):
            prices = _all_money(m)
            rent = None
            rm = re.search(r"(?:rent|@)\D{0,6}\$?\s*([\d,]+)", m)
            if rm: rent = float(rm.group(1).replace(",", ""))
            # address/apn lookup form: "underwrite 724 Patriots Point at $220k"
            price = max(prices) if prices else None
            if rent is None and len(prices) >= 2:
                rent = min(prices); price = max(prices)
            if price and price > 50000:
                rent = rent or 2200
                r = dispatch["underwrite"](price=price, monthly_rent=rent)
                return _resp(
                    f"**At {_fmt_money(price)} / {_fmt_money(rent)} rent: Cap {_fmt_pct(r['cap_rate'],2)}, "
                    f"CoC {_fmt_pct(r['coc'],2)}, DSCR {r['dscr']:.2f}.**\n"
                    f"**Confidence:** High · engine underwrite at the corrected cost basis.\n"
                    f"**Basis:** NOI {_fmt_money(r['noi'])} · cash in {_fmt_money(r['cash_invested'])}.",
                    [_kpis([("Cap rate", _fmt_pct(r['cap_rate'],2)), ("Cash-on-cash", _fmt_pct(r['coc'],2)),
                            ("DSCR", f"{r['dscr']:.2f}"), ("Monthly CF", _fmt_money(r['monthly_cf']))])])
        # 3) portfolio DCF
        if any(w in m for w in ["portfolio", "dcf", "irr", "fund", "100 home", "100-home"]):
            scen = "Downside" if "downside" in m or "stress" in m else "Upside" if "upside" in m else "Base"
            r = dispatch["portfolio_dcf"](scenario=scen)
            return _resp(
                f"**{scen} case: {_fmt_pct(r['levered_irr'])} levered IRR, {r['equity_multiple']:.2f}× equity, "
                f"{r['min_dscr']:.2f} min DSCR.**\n"
                f"**Confidence:** High · 10-yr levered model, exit on forward NOI.\n"
                f"**Basis:** unlevered IRR {_fmt_pct(r['unlevered_irr'])} · equity {_fmt_money(r['equity'])}.",
                [_kpis([("Levered IRR", _fmt_pct(r['levered_irr'])), ("Equity mult.", f"{r['equity_multiple']:.2f}×"),
                        ("Min DSCR", f"{r['min_dscr']:.2f}"), ("Going-in cap", _fmt_pct(r['going_in_cap'],2))])])
        # 3.5) site intelligence / highest-and-best-use / flood for an address
        if (any(w in m for w in ["best use", "highest and best", "what should i build", "what to build",
                "what fits", "site intel", "flood risk", "flood zone", "what's near", "whats near"])
                or ("flood" in m and re.search(r"\d", msg))):
            m2 = re.search(r"(?:\bfor|\bnear|\bat|\bof|\bon)\s+(.+)$", msg, re.I)
            cand = (m2.group(1) if m2 else msg)
            num = re.search(r"\d{1,6}\s+\S.*$", cand)        # narrow to a street address if present
            addr = (num.group(0) if num else cand).strip(" ?.,")
            g = dispatch["geocode"](query=addr)
            if not g.get("found"):
                g = dispatch["geocode"](query=msg)           # fallback: whole message
            if not g.get("found"):
                return _resp("**Couldn't locate that address.** Give a street address, city + state, or a zip.")
            s = dispatch["site_analysis"](lat=g["lat"], lon=g["lon"])
            if s.get("error"):
                return _resp("**" + s["error"] + "**")
            fl = s.get("flood", {}); hbu = s.get("hbu", []); fac = s.get("facilities", [])[:5]
            top = hbu[0] if hbu else None
            facline = " · ".join(f"{x['cat'].replace('_',' ')} {x['dist_mi']}mi" for x in fac)
            text = (f"**Best use: {top['use']} ({top['score']}/100)** near {(g.get('display') or '').split(',')[0]}.\n"
                    f"**Confidence:** Medium · market-signal screen, not a feasibility study.\n"
                    f"**Flood:** zone {fl.get('zone','?')} — {fl.get('risk','')}.\n"
                    f"**Why:** {' · '.join((top.get('why') or [])[:3]) if top else '—'}.\n"
                    f"**Nearest:** {facline}\n"
                    f"**Next:** click the pin on the Map for the full panel + build-out estimator.")
            blocks = [_kpis([(u['use'].split(' /')[0].split(' (')[0][:14], str(u['score'])) for u in hbu[:4]]),
                      _table(["Facility", "Distance"], [[x['cat'].replace('_', ' '), str(x['dist_mi']) + ' mi'] for x in fac])]
            return _resp(text, blocks)
        # 3.6) explain ONE deal — why this tier / what drives the score
        if (any(w in m for w in ["why", "explain", "breakdown", "what drives", "show the math",
                "how did", "walk me through", "score of", "calculation for", "justify"])
                and has_prop_ref and "explain_calc" in dispatch):
            r = dispatch["explain_calc"](query=(ap.group(1) if ap else msg))
            if r.get("found"):
                drv = " · ".join(f"{d['metric']} {d['contribution']:.0f}pt" for d in r["top_drivers"][:3])
                gate = "cleared the buy box" if r["gate_passed"] else "FAILED the buy box (scored 0)"
                return _resp(
                    f"**{r['address']} is {r['tier']} — total score {r['total_score']:.1f}.**\n"
                    f"**Confidence:** High · recomputed live{' and ties the stored score' if r['ties_stored'] else ''}.\n"
                    f"**Why:** it {gate}. Raw {r['raw_score']:.1f} × risk haircut {r['risk_haircut']:.3f}. "
                    f"Top drivers: {drv}.\n"
                    f"**Underwrite:** cap {_fmt_pct(r['cap_rate'],2)} · CoC {_fmt_pct(r['coc'],2)} · DSCR {r['dscr']:.2f} at {_fmt_money(r['offer_price'])}.\n"
                    f"**Next:** open Calculations for the full line-by-line trace, or download the working model.",
                    [_kpis([("Tier", r['tier'].split(' - ')[0]), ("Score", f"{r['total_score']:.1f}"),
                            ("Cap", _fmt_pct(r['cap_rate'],2)), ("DSCR", f"{r['dscr']:.2f}")])])
        # 3.7) risk of a specific deal
        if (any(w in m for w in ["risk of", "how risky", "risky", "risk profile", "risk grade",
                "red flag", "downside of", "what are the risks", "risks for", "risks of"])
                and has_prop_ref and "risk" in dispatch):
            r = dispatch["risk"](query=(ap.group(1) if ap else msg))
            if r.get("found"):
                flags = "\n".join(f"• **{x['severity']}** — {x['title']}" for x in r["top_flags"][:4])
                return _resp(
                    f"**{r['address']}: risk grade {r['grade']} ({r['score']}/100 — {r['band']}).**\n"
                    f"**Decision:** {r['decision']}\n{flags}\n"
                    f"**Next:** open the property drawer for all flags with mitigations + sources.")
        # 3.8) knowledge base — definitions, benchmarks, model concepts (no API key needed)
        if profile is not None:
            kb = knowledge.answer_kb(msg, profile, assumptions or {}, snapshot=None)
            if kb: return _resp(kb)
        # 4) targets / search
        if any(w in m for w in ["target", "top ", "show", "list", "find", "tier 1", "tier-1", "deals", "best"]):
            tier = "Tier 2 - Moderate" if "tier 2" in m else "Tier 3 - Watch" if "tier 3" in m else "Tier 1 - Strong"
            args = {"tier": tier, "limit": 8}
            st = _state(msg)
            if st: args["state"] = st
            if "under" in m:
                mp = _money(m.split("under", 1)[1])
                if mp and mp > 50000: args["max_price"] = mp
            yp = re.search(r"(?:yield|over)\D{0,4}([\d.]+)\s*%", m)
            if yp: args["min_yield"] = float(yp.group(1)) / 100
            r = dispatch["search_targets"](**args)
            if not r["rows"]:
                return _resp("**No targets match those filters.** Try loosening the state, price, or yield.")
            where = f" in {args['state']}" if st else ""
            top8 = r["rows"][:8]
            rows = [[x['address'], x['state'], _fmt_money(x['avm']), _fmt_pct(x['gross_yield']),
                     f"{x['total_score']:.0f}"] for x in top8]
            # flag issues on the suggestion set (don't recommend without caveats)
            sts = {x['state'] for x in top8}
            climate = sts & {"FL", "LA", "TX", "CA"}
            caveat = ["scores use an **AVM, not an appraisal** — re-trade against comps before LOI"]
            if climate: caveat.append(f"{', '.join(sorted(climate))}: bind a real **insurance** quote (climate/premium risk)")
            if len(sts) == 1: caveat.append(f"all in **{list(sts)[0]}** — single-state concentration")
            caveat.append("title / environmental / litigation aren't in the data — order reports in diligence")
            return _resp(
                f"**{r['count']} {tier} target(s){where}** — top {len(rows)} by score:\n"
                f"**Watch:** " + "; ".join(caveat) + ".\n"
                f"**Next:** open Targets to sort/underwrite, or ask me to underwrite a specific one.",
                [_table(["Address", "ST", "AVM", "Yield", "Score"], rows)])
        # 5) market summary / concentration
        if any(w in m for w in ["summary", "how many", "concentration", "hhi", "distribution", "pipeline", "overview", "risk"]):
            a = dispatch["market_summary"]()
            geo = " · ".join(f"{k} {v:,}" for k, v in list(a["tier1_top_states"].items())[:4])
            return _resp(
                f"**{a['tiers']['Tier 1 - Strong']:,} Tier-1 targets · {_fmt_pct(a['match_rate'])} of the universe clears the gate.**\n"
                f"**Confidence:** High · live counts over the scored universe.\n"
                f"**Basis:** geo HHI {a['hhi']:,} ({'highly concentrated' if a['hhi']>2500 else 'moderate'}). Top: {geo}.",
                [_kpis([("Tier 1", f"{a['tiers']['Tier 1 - Strong']:,}"), ("Match rate", _fmt_pct(a['match_rate'])),
                        ("Avg yield", _fmt_pct(a['tier1_avg_yield'])), ("Avg AVM", _fmt_money(a['tier1_avg_avm']))])])
        # 6) APN / address lookup
        ap = re.search(r"\b([A-Z0-9]{6,}|\d{2,}[A-Z]\d+)\b", msg)
        if ap or "address" in m:
            r = dispatch["lookup_property"](query=ap.group(1) if ap else msg)
            if r.get("found"):
                return _resp(
                    f"**{r['address']} — {r['tier']}, score {r['total_score']:.0f}.**\n"
                    f"**Basis:** {r['beds']}bd / {r['sqft']:.0f} sf · tenure {r['tenure']:.1f}y · click it in Targets for the full profile.",
                    [_kpis([("AVM", _fmt_money(r['avm'])), ("Market rent", _fmt_money(r['market_rent'])),
                            ("Gross yield", _fmt_pct(r['gross_yield'])), ("Score", f"{r['total_score']:.0f}")])])
        # 7) explain the model / methodology
        if any(w in m for w in ["explain", "how does", "how do", "how it works", "how the", "methodology",
                                "buy box", "buybox", "scoring model", "how are", "what drives", "how is the score"]):
            return _resp(
                "**Terra is a gated 4-pillar acquisition model.**\n"
                "**Gate:** a property must match the buy box — SFR, with year-built / sqft / AVM inside the bands of your "
                "187 closed deals, and in a rent-benchmarked zip. Fail the gate and it scores 0 (Not a Match).\n"
                "**Score 0-100** across four pillars: RETURN (gross yield 30 + price/margin 16), MOTIVATION "
                "(absentee/corporate 15 + ownership tenure 16), LOCATION (proven market 5 + cluster density 4 + metro "
                "target 4 + rent momentum 5), FIT (sqft 2.5 + year-built 2.5) — then a market-risk haircut.\n"
                "**Tier:** score ≥70 = Tier 1, ≥55 = Tier 2, matched but lower = Tier 3.\n"
                "**Next:** open Model Studio to reweight any metric, or ask me to score a specific deal.")
        # 8) best / highest-yield / cheapest
        if any(w in m for w in ["highest yield", "best yield", "top yield", "cheapest", "lowest price",
                                "best deal", "best return", "highest return", "most profitable"]):
            st = _state(msg); args = {"tier": "Tier 1 - Strong", "limit": 60}
            if st: args["state"] = st
            rows = dispatch["search_targets"](**args)["rows"]
            yld = any(w in m for w in ["yield", "return", "profitable"])
            rows = sorted(rows, key=lambda x: x.get("gross_yield" if yld else "avm", 0), reverse=yld)[:8]
            lab = "highest-yield" if yld else "lowest-priced"
            tbl = [[x['address'], x['state'], _fmt_money(x['avm']), _fmt_pct(x['gross_yield']), f"{x['total_score']:.0f}"] for x in rows]
            return _resp(f"**Top {len(tbl)} {lab} Tier-1 targets{(' in '+st) if st else ''}:**\n**Next:** open Targets for the full sortable list.",
                         [_table(["Address", "ST", "AVM", "Yield", "Score"], tbl)])
        # 9) smart router — attempt a useful action before any menu
        st = _state(msg)
        if st:
            r = dispatch["search_targets"](tier="Tier 1 - Strong", state=st, limit=8)
            if r.get("rows"):
                tbl = [[x['address'], x['state'], _fmt_money(x['avm']), _fmt_pct(x['gross_yield']), f"{x['total_score']:.0f}"] for x in r["rows"][:8]]
                return _resp(f"**{r['count']} Tier-1 targets in {st}** — top {len(tbl)} by score:", [_table(["Address", "ST", "AVM", "Yield", "Score"], tbl)])
        prices = _all_money(m)
        if prices and ("rent" in m or "$" in msg) and max(prices) > 40000:
            price = max(prices); rent = (min(prices) if len(prices) >= 2 else 2200)
            rr = dispatch["underwrite"](price=price, monthly_rent=rent)
            return _resp(f"**At {_fmt_money(price)} / {_fmt_money(rent)} rent: Cap {_fmt_pct(rr['cap_rate'],2)}, CoC {_fmt_pct(rr['coc'],2)}, DSCR {rr['dscr']:.2f}.**",
                         [_kpis([("Cap", _fmt_pct(rr['cap_rate'],2)), ("CoC", _fmt_pct(rr['coc'],2)), ("DSCR", f"{rr['dscr']:.2f}"), ("Monthly CF", _fmt_money(rr['monthly_cf']))])])
    except Exception as e:
        return _resp(f"(offline brain error: {e}) — try the tool buttons in each tab.")
    # fallback — concise, and points to full power
    return _resp("**I can run the engine for you.** Try one:\n"
            "• *Top Tier-1 targets in GA under $250k*\n"
            "• *Underwrite a $230k home at $2,200 rent*\n"
            "• *What price hits a 1.25 DSCR at $2,200 rent?*\n"
            "• *Best use and flood risk for [address]*\n"
            "• *Run the portfolio DCF downside case*\n"
            "• *How does the scoring model work?*\n\n"
            "For open-ended questions, set ANTHROPIC_API_KEY (Railway → Variables) to unlock full conversational ATLAS.")

# ----------------------------------------------------------- entry point
def ask(message, history, dispatch, snapshot, profile=None, assumptions=None):
    use_ai = bool(os.environ.get("ANTHROPIC_API_KEY"))
    try:
        import anthropic
    except ImportError:
        use_ai = False
    if not use_ai:
        out = offline_answer(message, dispatch, profile, assumptions); out["mode"] = "offline"; return out
    try:
        client = anthropic.Anthropic()
        spec = knowledge.model_spec(profile, assumptions, snapshot) if profile else ""
        sys = SYSTEM + "\n\n" + spec + "\n\nLIVE SNAPSHOT:\n" + json.dumps(snapshot, default=str)
        # history items must be {role, content:str}; drop anything malformed
        hist = [m for m in (history or []) if isinstance(m, dict) and m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)]
        msgs = hist[-12:] + [{"role": "user", "content": message}]
        for _ in range(6):
            resp = client.messages.create(model=MODEL, max_tokens=1600, system=sys, tools=TOOL_SPECS, messages=msgs)
            if resp.stop_reason == "tool_use":
                msgs.append({"role": "assistant", "content": resp.content})
                results = []
                for block in resp.content:
                    if block.type == "tool_use":
                        try:
                            out = dispatch[block.name](**block.input)
                        except Exception as e:
                            out = {"error": str(e)}
                        results.append({"type": "tool_result", "tool_use_id": block.id,
                                        "content": json.dumps(out, default=str)})
                msgs.append({"role": "user", "content": results})
                continue
            text = "".join(b.text for b in resp.content if b.type == "text")
            return {"text": text or "(no answer)", "blocks": [], "mode": "ai"}
        return {"text": "(stopped after tool budget)", "blocks": [], "mode": "ai"}
    except Exception as e:
        # never break — fall back to the deterministic brain with a note
        out = offline_answer(message, dispatch, profile, assumptions)
        out["text"] = out.get("text", "") + f"\n\n_ATLAS AI is temporarily unavailable ({str(e)[:90]}); answered from the deterministic engine._"
        out["mode"] = "offline-fallback"
        return out
