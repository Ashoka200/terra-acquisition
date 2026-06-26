"""
knowledge.py — ATLAS's training material: a structured, live knowledge base of the
ENTIRE acquisition model + a real-estate glossary with industry benchmarks.

Used two ways:
  * AI brain   → model_spec() is injected into the system prompt so the LLM answers
    every model/term question with the firm's actual numbers and logic (no guessing).
  * Offline brain → answer_kb() resolves definitions, benchmarks, "how is X computed",
    and "why does the model do Y" with NO API key.

Everything is generated from the LIVE profile + assumptions, so it never drifts from
what the engine actually runs.
"""

# ----------------------------------------------------------- glossary (with benchmarks)
GLOSSARY = {
    "cap rate": ("Going-in capitalization rate = Year-1 NOI ÷ all-in basis. The unlevered yield on the asset.",
                 "Rule of thumb: SFR rentals pencil around 5.5–7.5% going-in today. Below your debt rate ⇒ negative leverage."),
    "cash-on-cash": ("Cash-on-cash (CoC) = annual pre-tax cash flow ÷ cash invested. The levered cash yield on your equity.",
                     "Investors generally want 6–10%+. Below ~4% leaves no cushion for vacancy/repairs."),
    "coc": ("Cash-on-cash (CoC) = annual pre-tax cash flow ÷ cash invested.",
            "Target 6–10%+. Sub-4% is thin."),
    "dscr": ("Debt-service coverage ratio = NOI ÷ annual debt service. How many times income covers the mortgage.",
             "Lenders require ≥1.20–1.25x for SFR/DSCR loans. <1.0 means the rent doesn't cover the debt."),
    "noi": ("Net operating income = effective gross income − operating expenses (before debt service & capex).",
            "The single most important number; everything keys off it."),
    "egi": ("Effective gross income = gross scheduled rent × (1 − vacancy).", ""),
    "gsr": ("Gross scheduled rent = monthly rent × 12 at 100% occupancy.", ""),
    "gross yield": ("Gross yield = annual market rent ÷ acquisition basis. A quick income screen before expenses.",
                    "The model scores yield from a floor to a target band; higher is better."),
    "avm": ("Automated valuation model — a statistical price estimate (not an appraisal).",
            "Always re-trade against comps/BPO before hard money; AVMs carry error."),
    "irr": ("Internal rate of return — the annualized, time-weighted return that sets NPV to zero over the hold.",
            "The model targets ~14–15% levered IRR on the Tier-1 portfolio base case."),
    "equity multiple": ("Equity multiple = total cash returned ÷ equity invested (un-annualized).",
                        "A 2.0x over a 7-yr hold ≈ doubling your money; the base case runs ~2.3x."),
    "ltv": ("Loan-to-value = loan ÷ price. How much leverage you take.",
            "The model defaults to 70% LTV; lower LTV lifts DSCR and lowers risk."),
    "amortization": ("The schedule over which the loan principal is repaid (e.g. 30 years).", ""),
    "io": ("Interest-only — a period where you pay only interest, no principal. Boosts early cash flow, no amortization.",
           "The portfolio model runs 5 IO years then amortizes."),
    "vacancy": ("Vacancy allowance — the % of gross rent lost to empty units/turnover.",
                "The model uses 5%; in soft submarkets stress to 8–10%."),
    "opex": ("Operating expenses — management, maintenance, taxes, insurance, HOA, other (NOT debt or capex).", ""),
    "capex": ("Capital expenditures — roof, HVAC, big-ticket replacements. Reserved separately from opex.",
              "The model reserves $1,200/home/yr."),
    "exit cap": ("Exit (reversion) cap rate — the cap applied to forward NOI to estimate the sale price.",
                 "Higher exit cap = more conservative; the model uses 6.5%."),
    "absentee": ("An owner who doesn't occupy the property (often out-of-state or corporate) — a motivation signal.",
                 "Absentee/corporate owners transact more readily; the model rewards it."),
    "tenure": ("Years the current owner has held the property (proxy for equity & motivation).",
               "Long tenure ⇒ more equity & more likely to sell; scored on a saturating curve."),
    "gate": ("The buy-box hard filter. Miss any band (type, year, sqft, AVM, rent-benchmarked zip) ⇒ Not a Match, score 0.", ""),
    "tier": ("The output bucket from the total score: Tier 1 (strong), Tier 2 (moderate), Tier 3 (watch), or Not a Match.", ""),
    "pillar": ("One of the four scoring themes: Return, Motivation, Location, Fit.", ""),
    "haircut": ("Risk haircut — a market-risk discount applied to the raw score so riskier metros score lower.", ""),
    "mcda": ("Multi-criteria decision analysis — scoring a deal on weighted criteria instead of one metric.", ""),
    "going-in cap": ("The cap rate at purchase (Year-1 NOI ÷ all-in).", ""),
    "absorption": ("How fast available units lease up in a market.", ""),
    "1% rule": ("A screen: monthly rent ≥ 1% of price. A fast proxy for cash flow, not a substitute for underwriting.",
                "Hard to hit in appreciating Sun-Belt metros; the model underwrites fully instead."),
}
ALIASES = {"cash on cash": "cash-on-cash", "debt service coverage": "dscr", "debt coverage": "dscr",
           "net operating income": "noi", "yield": "gross yield", "loan to value": "ltv",
           "interest only": "io", "interest-only": "io", "cap": "cap rate", "internal rate of return": "irr"}


def _pct(x, d=1):
    try: return f"{float(x)*100:.{d}f}%"
    except Exception: return str(x)

def _money(x):
    try: return f"${float(x):,.0f}"
    except Exception: return str(x)


def model_spec(profile, assumptions, snapshot=None):
    """A complete, accurate description of the live model — injected into the AI prompt."""
    g = {c["field"]: c for c in profile["gate"]}
    mx = {m["key"]: m for m in profile["metrics"]}
    w = lambda k: mx[k]["weight"] if k in mx else 0
    yb, sq, av = g.get("yearbuilt", {}), g.get("sqft", {}), g.get("avm", {})
    yld = mx.get("yield", {}).get("norm", {})
    a = assumptions
    by_pillar = {}
    for m in profile["metrics"]:
        by_pillar.setdefault(m["pillar"], []).append(f'{m["label"]} {m.get("weight",0):g}')
    pil = "  ".join(f'{k} ({", ".join(v)})' for k, v in by_pillar.items())
    snap = ""
    if snapshot:
        t = snapshot.get("tiers", {})
        snap = (f'\nLIVE UNIVERSE: {snapshot.get("rows","?")} rows · '
                f'Tier 1 {t.get("Tier 1 - Strong","?")}, Tier 2 {t.get("Tier 2 - Moderate","?")}, '
                f'match rate {_pct(snapshot.get("match_rate",0))}.')
    return f"""=== TERRA ACQUISITION MODEL (live spec — answer from THIS, never invent numbers) ===
A gated, four-pillar MCDA scoring model over a single-family-rental universe, plus a
two-way underwriter and a 10-year portfolio DCF. It is a 100%-parity port of the firm's
Excel model; you sit on the deterministic engine — call tools for any number on a
specific deal, and use the spec below to explain the logic.

STEP 1 — BUY BOX GATE (hard filter; fail any line ⇒ "Not a Match", score 0):
  • Property type = SFR
  • Year built between {yb.get('lo','?'):g}–{yb.get('hi','?'):g}
  • Living area between {sq.get('lo','?'):g}–{sq.get('hi','?'):g} sqft
  • AVM (price proxy) between {_money(av.get('lo',0))}–{_money(av.get('hi',0))}
  • Zip is rent-benchmarked (in the firm's rent reference)
  These bands are empirical — calibrated to the firm's 187 closed deals.

STEP 2 — SCORE 0–100 across four pillars (weights are live and editable in Model Studio):
  {pil}
  Each metric maps its input to a 0–100 sub-score via a normalization:
   - Gross yield: scaled from floor {_pct(yld.get('lo',0))} to target {_pct(yld.get('hi',0))} (higher better).
   - Price/Margin: inverse-scaled across the AVM band (lower price better).
   - Absentee/Corporate: 100 if corporate-owned else 0.
   - Tenure: saturating ratio toward the saturation point (more years ⇒ higher, capped).
   - Location (Proven/Density/Target/Momentum): precomputed 0–100 market indices by zip/MSA.
   - Sqft Fit: triangular — closeness to the band center.
   - Year Fit: scaled across the year band.
  raw = Σ(sub-score × weight) ÷ Σ(weight).
  RISK HAIRCUT: total = raw × [1 − max(0, marketRisk − baseline {profile['risk']['baseline']:g}) /
   (100 − baseline) × sensitivity {profile['risk']['sensitivity']:g}]. Riskier metros score lower.
  TIERS: Tier 1 ≥ {profile['tiers']['tier1']:g}; Tier 2 ≥ {profile['tiers']['tier2']:g}; matched-but-lower = Tier 3.

STEP 3 — UNDERWRITING (live assumptions; reverse goal-seek also available):
  all-in = price×(1+closing {_pct(a['closing'])})+rehab {_money(a['rehab'])}; loan = price×LTV {_pct(a['ltv'])};
  EGI = rent×12×(1−vacancy {_pct(a['vacancy'])}); opex = mgmt {_pct(a['pm'])} + maint {_pct(a['maint'])} +
  tax {_pct(a['tax'])} + insurance {_money(a['ins'])} + other {_money(a['other'])}; NOI = EGI − opex;
  debt service = PMT(rate {_pct(a['rate'])}, {int(a['amort'])}y) ; cap = NOI/all-in; CoC = cashflow/cash;
  DSCR = NOI/debt service. Reverse mode solves the price that hits a target cap/CoC/DSCR.

DATA CORRECTIONS already applied (the model is the corrected one): basis = 90% of AVM (not list),
market rent realized at ~95% of the max-rent bucket, per-state taxes/insurance (not flat),
$1,200 capex/home. Beds are actual when supplied, else sqft-estimated (flagged).{snap}

TONE: bottom-line first, specific, honest about confidence and data limits. Never fabricate
litigation/title/environmental data — those require ordered reports."""


def _find_term(m):
    m = " " + m + " "
    for alias, canon in ALIASES.items():
        if alias in m: return canon
    # longest key first so "going-in cap" beats "cap"
    for term in sorted(GLOSSARY, key=len, reverse=True):
        if " " + term + " " in m or m.strip().endswith(term) or m.strip() == term:
            return term
    return None


def answer_kb(msg, profile, assumptions, snapshot=None):
    """Offline KB resolver. Returns a markdown string or None (so the router can continue)."""
    import re
    # defer to the compute intents when the question is about a SPECIFIC deal / number
    if re.search(r"\$\s*\d", msg) or re.search(r"\b\d{2,}[A-Z]\d|\b[A-Z0-9]{8,}\b", msg) or \
       any(w in msg.lower() for w in ["underwrite", "pay for", "price to", "how much can", "price that",
            "run the", "downside", "stress", "top ", "show me", "list ", "find me"]):
        return None
    m = re.sub(r"[^a-z0-9 %$.-]", " ", msg.lower()).strip()
    m = re.sub(r"\s+", " ", m)
    defish = any(w in m for w in ["what is", "what's", "whats", "what are", "define", "definition",
                "mean", "meaning", "explain", "good", "healthy", "benchmark", "rule of thumb",
                "ideal", "typical", "should", "how much", "what makes"])
    # 1) glossary term
    term = _find_term(m)
    if term and (defish or len(m.split()) <= 4):
        d, bench = GLOSSARY[term]
        out = f"**{term.title()} —** {d}"
        if bench: out += f"\n**Benchmark:** {bench}"
        out += "\n**In Terra:** open Calculations on any deal to see this computed line-by-line."
        return out
    # 2) model-topic questions
    if any(w in m for w in ["buy box", "buybox", "gate", "what filters", "qualif"]):
        g = {c["field"]: c for c in profile["gate"]}
        yb, sq, av = g.get("yearbuilt", {}), g.get("sqft", {}), g.get("avm", {})
        return (f"**The Buy Box is a hard gate — miss any line and the deal scores 0 (Not a Match).**\n"
                f"**Type:** SFR · **Year built:** {yb.get('lo','?'):g}–{yb.get('hi','?'):g} · "
                f"**Sqft:** {sq.get('lo','?'):g}–{sq.get('hi','?'):g} · "
                f"**AVM:** {_money(av.get('lo',0))}–{_money(av.get('hi',0))} · **Zip:** must be rent-benchmarked.\n"
                f"**Why:** the bands are calibrated to the firm's 187 closed deals.\n"
                f"**Next:** edit any band in Model Studio, or trace a deal in Calculations.")
    if any(w in m for w in ["weight", "pillar", "how is the score", "how are scores", "scoring model",
                            "how does scoring", "how do you score", "four pillar"]):
        by = {}
        for mm in profile["metrics"]:
            by.setdefault(mm["pillar"], []).append(f'{mm["label"]} {mm.get("weight",0):g}')
        lines = "\n".join(f"• **{k}:** {', '.join(v)}" for k, v in by.items())
        return (f"**Score = weighted sub-scores across four pillars, × a market-risk haircut.**\n{lines}\n"
                f"**Tiers:** ≥{profile['tiers']['tier1']:g} Tier 1, ≥{profile['tiers']['tier2']:g} Tier 2, lower = Tier 3.\n"
                f"**Next:** Calculations shows each sub-score and contribution for a specific deal.")
    if any(w in m for w in ["risk haircut", "haircut", "risk overlay", "risk adjust"]):
        r = profile["risk"]
        return (f"**The risk haircut discounts the raw score in riskier metros.**\n"
                f"**Formula:** total = raw × [1 − max(0, marketRisk − {r['baseline']:g}) / (100 − {r['baseline']:g}) × {r['sensitivity']:g}].\n"
                f"**Effect:** a low-risk metro keeps ~all its score; a high-risk one is shaved up to {_pct(r['sensitivity'])}.\n"
                f"**Next:** tune baseline/sensitivity in Model Studio → Tiers & risk.")
    if any(w in m for w in ["assumption", "tax rate", "insurance", "vacancy", "avm discount",
                            "rent realiz", "what rate", "closing", "rehab", "what cap", "expenses"]):
        a = assumptions
        return (f"**Underwriting assumptions (editable in Model Studio):**\n"
                f"• Basis: **90% of AVM** · rent realized ~**95%** of the max-rent bucket\n"
                f"• Vacancy **{_pct(a['vacancy'])}** · Mgmt **{_pct(a['pm'])}** · Maint **{_pct(a['maint'])}** · "
                f"Tax **{_pct(a['tax'])}** · Insurance **{_money(a['ins'])}** · Other **{_money(a['other'])}**\n"
                f"• Financing: LTV **{_pct(a['ltv'])}** · Rate **{_pct(a['rate'])}** · Amort **{int(a['amort'])}y** · "
                f"Points **{_pct(a['points'])}** · Closing **{_pct(a['closing'])}** · Rehab **{_money(a['rehab'])}**\n"
                f"**Why:** per-state taxes/insurance replaced the old flat figures — a key correction.")
    if any(w in m for w in ["how is yield", "how do you calc", "how is yield computed", "yield calc",
                            "how is the yield", "compute yield"]):
        return ("**Gross yield = annual market rent ÷ acquisition basis (90% of AVM).**\n"
                "Market rent = the zip+bed max-rent bucket realized at ~95%. The score then scales yield "
                "from the floor to the target band (higher is better).\n"
                "**Next:** Calculations shows the exact numbers for any deal.")
    return None
