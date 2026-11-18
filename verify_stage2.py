"""Verification of Stage 2 pure functions. Run: py -3.12 verify_stage2.py"""
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, ".")
from tasi_agent.position_sizing import calculate_position_size, portfolio_risk_check
from tasi_agent.trade_plans import classify_action

results = []

def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


print("\n=== Position sizing ===")
r = calculate_position_size(capital=100_000, risk_pct=0.01,
                             entry=100.0, stop=95.0)
check("risk-based shares: 1% of 100k / 5 SAR = ~200 shares",
      190 <= r["shares"] <= 200, f"got {r['shares']}")
check("actual risk under 1%", r["actual_risk_pct"] <= 0.0105,
      f"{r['actual_risk_pct']*100:.3f}%")
check("position value <= capital", r["position_value"] <= 100_000)

r = calculate_position_size(capital=100_000, risk_pct=0.01,
                             entry=100.0, stop=105.0)
check("invalid when stop >= entry", not r["valid"])

r = calculate_position_size(capital=500, risk_pct=0.01,
                             entry=1000.0, stop=950.0)
check("invalid when capital < 1 share", not r["valid"])

r = calculate_position_size(capital=10_000, risk_pct=0.05,
                             entry=100.0, stop=99.0)
check("shares limited by capital not risk", r["shares"] <= 100,
      f"got {r['shares']}")

positions = [{"max_loss": 900, "position_value": 20_000},
             {"max_loss": 1200, "position_value": 25_000}]
p = portfolio_risk_check(positions, capital=100_000,
                          max_total_risk_pct=0.05, max_positions=5)
check("portfolio risk within limits", p["risk_ok"])
check("portfolio exposure within limits", p["exposure_ok"])

positions.append({"max_loss": 4000, "position_value": 40_000})
p = portfolio_risk_check(positions, capital=100_000,
                          max_total_risk_pct=0.05, max_positions=5)
check("portfolio risk exceeds after adding", not p["risk_ok"])


print("\n=== Trade action classification ===")
r = classify_action(setup_fired_today=True,
                    setup_name="volume_breakout",
                    market_regime="BULLISH",
                    opportunity_score=75.0, eligible=True)
check("BUY_NOW when setup+score+regime align", r["action"] == "BUY_NOW")

r = classify_action(setup_fired_today=True,
                    setup_name="volume_breakout",
                    market_regime="BULLISH",
                    opportunity_score=45.0, eligible=True)
check("WATCH when score < 60", r["action"] == "WATCH")

r = classify_action(setup_fired_today=True,
                    setup_name="volume_breakout",
                    market_regime="BEARISH",
                    opportunity_score=85.0, eligible=True)
check("NO_TRADE_MARKET overrides high score in bearish regime",
      r["action"] == "NO_TRADE_MARKET")

r = classify_action(setup_fired_today=False,
                    setup_name="ema20_pullback",
                    market_regime="BULLISH",
                    opportunity_score=0, eligible=True)
check("BUY_ON_PULLBACK when pullback forming",
      r["action"] == "BUY_ON_PULLBACK")

r = classify_action(setup_fired_today=False,
                    setup_name="volume_breakout",
                    market_regime="BULLISH",
                    opportunity_score=0, eligible=True)
check("BUY_ON_BREAKOUT when breakout forming",
      r["action"] == "BUY_ON_BREAKOUT")

r = classify_action(setup_fired_today=True,
                    setup_name="volume_breakout",
                    market_regime="BULLISH",
                    opportunity_score=90.0, eligible=False)
check("AVOID when eligibility fails", r["action"] == "AVOID")


print("\n=== Setup detection (synthetic) ===")
from tasi_agent.indicators import compute_all
from tasi_agent.technical_setups import detect_volume_breakout

n = 60
close = np.full(n, 100.0)
close[-3:] = [101, 103, 106]
high = close * 1.005
low = close * 0.995
volume = np.full(n, 1000)
volume[-1] = 3000

df = pd.DataFrame({
    "open": close, "high": high, "low": low,
    "close": close, "volume": volume,
}, index=[f"2026-01-{i+1:02d}" if i < 31 else f"2026-02-{i-30:02d}"
          for i in range(n)])
ind = compute_all(df)

sig = detect_volume_breakout(ind, lookback=20, volume_mult=1.5)
check("volume breakout fires on last bar with volume spike",
      bool(sig.iloc[-1]))


print("\n" + "=" * 60)
passed = sum(1 for _, c in results if c)
failed = sum(1 for _, c in results if not c)
print(f"SUMMARY: {passed} passed · {failed} failed · {len(results)} total")
print("=" * 60)
if failed == 0:
    print("\n[STAGE 2 VERIFIED]")
    sys.exit(0)
print("\n[FAILURES]")
for name, c in results:
    if not c:
        print(f"  - {name}")
sys.exit(1)