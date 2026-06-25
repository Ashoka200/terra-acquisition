"""
assistant.py — ATLAS: the in-app acquisition copilot (mirrors LUMEN).
Two brains:
  * OFFLINE brain (rule-based intent router) — works with NO API key. Parses the
    question, calls the deterministic engine tools, formats a LUMEN-style answer.
  * AI brain (Anthropic tool-use loop) — used when ANTHROPIC_API_KEY is set, for
    richer free-form questions. Still grounded: the LLM calls tools, never computes.
"""
import os, re, json

MODEL = os.environ.get("ATLAS_MODEL", "claude-opus-4-8")

SYSTEM = """You are ATLAS, the residential-acquisition copilot for United Brothers.
You sit on a deterministic Python engine that is a 100%-parity port of the firm's Excel
acquisition model. NEVER do math yourself — always call a tool and report what it returns.
Answer bottom-line-first: 1) bold one-line answer, 2) **Confidence:** one word,
3) **Why:** 2-3 short reasons, 4) **Detail:** key numbers, 5) **Next:** one action.
Tables only on explicit request."""

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

def offline_answer(msg, dispatch):
    m = msg.lower()
    try:
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
            rows = [[x['address'], x['state'], _fmt_money(x['avm']), _fmt_pct(x['gross_yield']),
                     f"{x['total_score']:.0f}"] for x in r["rows"][:8]]
            return _resp(
                f"**{r['count']} {tier} target(s){where}** — top {len(rows)} by score:\n"
                f"**Next:** open Targets to sort, or the Map to see them clustered.",
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
    except Exception as e:
        return _resp(f"(offline brain error: {e}) — try the tool buttons in each tab.")
    # fallback menu
    return _resp("**I run the deterministic engine.** Ask me to:\n"
            "• *Top Tier-1 targets in GA under $250k*\n"
            "• *Underwrite a $230k home at $2,200 rent*\n"
            "• *What price hits a 1.25 DSCR at $2,200 rent?*\n"
            "• *Run the portfolio DCF downside case*\n"
            "• *Pipeline summary / concentration*\n"
            "_(Set ANTHROPIC_API_KEY for free-form questions.)_")

# ----------------------------------------------------------- entry point
def ask(message, history, dispatch, snapshot):
    use_ai = bool(os.environ.get("ANTHROPIC_API_KEY"))
    try:
        import anthropic
    except ImportError:
        use_ai = False
    if not use_ai:
        out = offline_answer(message, dispatch); out["mode"] = "offline"; return out
    try:
        client = anthropic.Anthropic()
        sys = SYSTEM + "\n\nLIVE SNAPSHOT:\n" + json.dumps(snapshot)
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
        out = offline_answer(message, dispatch)
        out["text"] = out.get("text", "") + f"\n\n_ATLAS AI is temporarily unavailable ({str(e)[:90]}); answered from the deterministic engine._"
        out["mode"] = "offline-fallback"
        return out
