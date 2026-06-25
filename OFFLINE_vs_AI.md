# Offline app vs. AI agent — how to build this, and the recommendation

You asked how to build this **offline** vs as an **AI agent**. They are not either/or —
the right design is a deterministic core with an AI layer on top. Here is the decision
framework and what this scaffold implements.

## The two layers

| | **Offline core (deterministic)** | **AI agent layer (ATLAS / LUMEN-style)** |
|---|---|---|
| What it is | `re_engine.py` + `re_underwrite.py` — the scoring, buy box, DCF, reverse solver | `assistant.py` — an LLM that *chooses which tool to run* and explains the result |
| Determinism | 100% reproducible; same input → same output (parity-tested) | The math is still deterministic (it calls the core); only the *language* is generative |
| Needs internet / API key | No | Yes (`ANTHROPIC_API_KEY`) for chat; tools still run offline |
| Auditability | Total — every number traces to a formula | Grounded: the model must cite tool output, never compute |
| Best for | Batch scoring, the workbook, REST endpoints, nightly refresh | "Show me Tier-1 in GA under $250k", "what price hits a 1.25x DSCR?", narrative memos |
| Failure mode | A wrong formula (caught by the eval gate) | A hallucinated number (prevented by forcing tool-use; never let the LLM do math) |

## Recommendation

**Build the offline core first and make it the single source of truth; add the AI agent as a thin, tool-using layer.** This is exactly how your Sales Rate Agent works — LUMEN sits on top of `engine.py` and is told its job is to call the engine, not to invent numbers. We mirrored that:

1. **Core = the Excel model, ported and parity-tested.** 100% match to the workbook (scoring, DCF). This is what you trust to write checks against.
2. **REST tools** expose the core (`/api/search_targets`, `/api/underwrite`, `/api/reverse_solve`, `/api/portfolio_dcf`) — usable from the UI, scripts, or Excel/Power Query **with no AI at all**.
3. **ATLAS** (the chatbot) is optional. Turn it off (no key) and you still have a full working app. Turn it on and you get natural-language access to the same tools.

### Why not "pure AI"?
Never let an LLM do the underwriting arithmetic — it will occasionally be confidently wrong, and in acquisition that is real money. The LLM's job is **routing + explanation**, not calculation. (Your Sales Rate Agent learned this: LUMEN once *hallucinated* an Occ% formula until it was forced to read the real engine logic.)

### Why not "pure offline"?
Because the value of an agent is letting non-analysts ask in plain English and get a grounded, sourced answer — and letting it pull live web context (rates, insurance, regulation) on request. That is the LUMEN pattern and it is worth having.

## Maturity path (mirrors the Sales Rate Agent)
1. **This scaffold** — engine + REST + UI + ATLAS stub + eval gate. ✅
2. **Data-integrity gate** — `data_guard.py` (fail-closed) for every new ATTOM/Deal-History/Rent upload.
3. **Auth + RBAC** — Entra SSO + OTP + invites; capabilities (Admin/Analyst/Viewer).
4. **Atomic refresh** — `refresh()` re-scores a new universe and rewrites the workbook table + KPI stamp.
5. **Web + attachments** — ATLAS reads pasted links / uploaded PDFs (rent comps, tax bills) and grounds answers.
6. **Ledger + feedback** — log every underwrite + thumbs up/down → tune weights once conversion outcomes exist.
7. **Deploy** — Railway (`Procfile`), volume-mounted `data/`, daily backups.
