"""Verification of indicator correctness. Run: py -3.12 verify_indicators.py"""
import sys
import pandas as pd
import numpy as np

sys.path.insert(0, ".")
from tasi_agent.indicators import (
    sma, ema, rsi, macd, atr, bollinger_bands, obv, volume_ratio, adx,
)

results = []

def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


print("\n=== SMA ===")
s = pd.Series([1.0, 2, 3, 4, 5, 6, 7, 8, 9, 10])
r = sma(s, 5)
check("SMA(5) idx4 = 3", abs(r.iloc[4] - 3.0) < 0.001)
check("SMA(5) idx9 = 8", abs(r.iloc[9] - 8.0) < 0.001)
check("SMA(5) warmup idx3 is NaN", pd.isna(r.iloc[3]))

print("\n=== EMA ===")
s = pd.Series([1.0] * 20 + [10.0] * 40)
r = ema(s, 5)
check("EMA(5) pre-jump ~ 1", abs(r.iloc[19] - 1.0) < 0.01)
check("EMA(5) post-jump converges to 10", abs(r.iloc[59] - 10.0) < 0.1)

print("\n=== RSI ===")
up = pd.Series(range(1, 51))
r_up = rsi(up, 14)
check("RSI on pure uptrend = 100", r_up.iloc[-1] > 99.9)

down = pd.Series(range(50, 0, -1))
r_down = rsi(down, 14)
check("RSI on pure downtrend = 0", r_down.iloc[-1] < 0.1)

flat = pd.Series([100.0] * 50)
r_flat = rsi(flat, 14)
check("RSI on flat series is NaN", pd.isna(r_flat.iloc[-1]))

alt = pd.Series([100.0, 101, 100, 101, 100, 101, 100, 101] * 10)
r_alt = rsi(alt, 14)
check("RSI on alternating series ~ 50", 30 < r_alt.iloc[-1] < 70)

print("\n=== MACD ===")
s = pd.Series([100.0] * 100)
m = macd(s)
check("MACD on constant = 0", abs(m['macd'].iloc[-1]) < 1e-6)
check("MACD signal on constant = 0", abs(m['signal'].iloc[-1]) < 1e-6)
check("MACD histogram on constant = 0", abs(m['histogram'].iloc[-1]) < 1e-6)

print("\n=== ATR ===")
df = pd.DataFrame({
    "high": [101.0] * 50,
    "low": [99.0] * 50,
    "close": [100.0] * 50,
})
a = atr(df, 14)
check("ATR on constant range 2 = 2", abs(a.iloc[-1] - 2.0) < 0.01)

df2 = pd.DataFrame({
    "high": [101.0] * 50,
    "low": [99.0] * 50,
    "close": [101.0] * 49 + [100.0],
})
check("ATR unaffected by same-range close", abs(atr(df2, 14).iloc[-1] - 2.0) < 0.15)

print("\n=== Bollinger ===")
s = pd.Series([100.0] * 50)
bb = bollinger_bands(s, 20, 2.0)
check("BB upper on constant = 100", abs(bb["upper"].iloc[-1] - 100.0) < 0.001)
check("BB lower on constant = 100", abs(bb["lower"].iloc[-1] - 100.0) < 0.001)
check("BB width on constant = 0", abs(bb["width_pct"].iloc[-1]) < 0.001)

print("\n=== OBV ===")
df = pd.DataFrame({
    "close": [10.0, 11, 10, 11, 12],
    "volume": [100, 200, 150, 300, 250],
})
o = obv(df)
expected = [0, 200, 50, 350, 600]
check("OBV correct on 5-day sequence",
      all(abs(o.iloc[i] - expected[i]) < 0.001 for i in range(5)))

print("\n=== Volume ratio ===")
# Test 1: exact 2x case with 20 prior days
df = pd.DataFrame({"volume": [100.0] * 20 + [200.0]})
vr = volume_ratio(df, 20)
check("Volume ratio = 2.0 when today is 2x prior 20-day avg",
      abs(vr.iloc[-1] - 2.0) < 0.01, f"got {vr.iloc[-1]:.4f}")

# Test 2: 3x case with non-uniform prior volumes
volumes = list(range(90, 110))  # 20 values averaging 99.5
today_volume = 99.5 * 3          # 298.5
df2 = pd.DataFrame({"volume": volumes + [today_volume]})
vr2 = volume_ratio(df2, 20)
check("Volume ratio = 3.0 when today is 3x prior avg",
      abs(vr2.iloc[-1] - 3.0) < 0.01, f"got {vr2.iloc[-1]:.4f}")

# Test 3: warmup behavior — needs 20 prior days + 1 current = 21 rows
df3 = pd.DataFrame({"volume": [100.0] * 20})  # only 20 rows = 19 prior days
vr3 = volume_ratio(df3, 20)
check("Volume ratio is NaN with only 19 prior days",
      pd.isna(vr3.iloc[-1]))

# Test 4: normal volume returns ~1.0
df4 = pd.DataFrame({"volume": [100.0] * 21})
vr4 = volume_ratio(df4, 20)
check("Volume ratio = 1.0 when today matches prior avg",
      abs(vr4.iloc[-1] - 1.0) < 0.01, f"got {vr4.iloc[-1]:.4f}")

print("\n=== ADX ===")
up_df = pd.DataFrame({
    "high": [100.0 + i * 0.5 for i in range(50)],
    "low":  [99.0 + i * 0.5 for i in range(50)],
    "close":[99.5 + i * 0.5 for i in range(50)],
})
adx_df = adx(up_df)
check("ADX +DI > -DI in uptrend",
      adx_df["plus_di"].iloc[-1] > adx_df["minus_di"].iloc[-1])
check("ADX > 25 in strong uptrend", adx_df["adx"].iloc[-1] > 25)

print("\n" + "=" * 60)
passed = sum(1 for _, c in results if c)
failed = sum(1 for _, c in results if not c)
print(f"SUMMARY: {passed} passed · {failed} failed · {len(results)} total")
print("=" * 60)
if failed == 0:
    print("\n[ALL INDICATORS VERIFIED]")
    sys.exit(0)
print("\n[FAILURES]")
for name, c in results:
    if not c:
        print(f"  - {name}")
sys.exit(1)