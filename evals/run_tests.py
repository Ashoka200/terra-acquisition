"""
run_tests.py — the gate. Offline, deterministic. Mirrors the Sales Rate Agent's
parity/invariant gates. Run before any deploy:  python evals/run_tests.py
"""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pandas as pd, numpy as np
import re_engine as E, re_underwrite as U

DATA = os.path.join(os.path.dirname(__file__), "..", "..", "data")
P, F = 0, 0
def ok(name, cond):
    global P, F
    print(("  PASS " if cond else "  FAIL ") + name); P += cond; F += (not cond)

print("== 1. scoring parity vs Excel cached ==")
refs = E.load_refs(DATA)
uni = pd.read_parquet(os.path.join(DATA, "universe_raw.parquet"))
sc = E.score_universe(uni, refs)              # parity mode
ok("match count = 284932", int(sc["match"].sum()) == 284932)
ok("Tier 1 = 16063", int((sc["tier"] == "Tier 1 - Strong").sum()) == 16063)
ok("tier agreement = 100%", (sc["tier"].values == uni["x_tier"].astype(str).values).mean() == 1.0)
d = (pd.to_numeric(sc.loc[sc["match"]==1,"total_score"]) -
     pd.to_numeric(uni.loc[sc["match"]==1,"x_total"])).abs().max()
ok("total_score max abs diff < 1e-9", d < 1e-9)

print("== 2. reverse solver round-trips to target ==")
a = dict(closing=0.03, rehab=15000, vacancy=0.05, pm=0.08, maint=0.05, tax=0.011,
         ins=1400, hoa=0, other=300, ltv=0.70, rate=0.0725, amort=30, points=0.01)
for tm, tgt, key in [("cap",0.07,"cap_rate"),("dscr",1.25,"dscr"),("coc",0.08,"coc")]:
    Pr = U.reverse_price(tm, tgt, a, 3200)
    got = U.underwrite(Pr, 3200, a)[key]
    ok(f"reverse {tm} -> {tgt} (got {got:.4f})", abs(got - tgt) < 1e-6)

print("== 3. portfolio DCF ties to workbook base case ==")
base = U.portfolio_dcf(dict(homes=100, acq=0.02, rehab=5000, rent_growth=0.03,
    exp_growth=0.025, vacancy=0.05, pm=0.08, rm=0.05, other_home=300, hoa_home=0,
    ltv=0.70, rate=0.0725, amort=30, io_years=5, hold=7, exit_cap=0.065, selling=0.02,
    loan_fee=0.01, price_home=269160, rent_home=2407, tax=0.011, ins_home=1400, capex_home=300))
ok("levered IRR ~ 14.9%", abs(base["levered_irr"] - 0.1487) < 0.002)
ok("equity multiple ~ 2.37", abs(base["equity_multiple"] - 2.374) < 0.01)
ok("min DSCR ~ 1.41", abs(base["min_dscr"] - 1.406) < 0.01)

print("== 4. app endpoints (offline tools) ==")
import app as APP
ok("market_summary returns tiers", "tiers" in APP.t_market_summary())
ok("analytics has hhi + histograms", set(["hhi","yield_hist","state_shares"]) <= set(APP.t_analytics()))
mp = APP.t_map_points(state="GA", limit=5)
ok("map_points returns GA coords", mp["count"] > 0 and len(mp["points"][0]) == 8)
pr = APP.t_portfolio_dcf(scenario="Downside")
ok("portfolio scenario + series + compare", "series" in pr and "compare" in pr and len(pr["series"]["years"]) == 8)
ok("search_targets returns lat/lon", "lat" in APP.t_search_targets(limit=1)["rows"][0])

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
