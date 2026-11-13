"""nvidia-ceo-forecast: Polymarket "Next CEO of NVIDIA?" 4-hour-ahead quantile forecaster.

Model (per market, from its own 72h of 1-minute Yes prices):
  * median: the last price, eased toward a cross-market total of 1 (the 24
    outcomes are mutually exclusive, so when the Yes prices sum above 1 they
    tend to come down, and vice versa), phased in over the horizon.
  * spread: a per-market sigma from the winsorized mean |1-minute move| over
    the last 12 hours, scaled. Winsorizing (not RMS) is the key choice: these
    prices are mostly flat with rare enormous jumps (a market listing at 0.48
    and collapsing), and an RMS/EWMA of squared moves lets one jump inflate
    the band for hours. Quantiles are median + z_tau * sigma * sqrt(h), with
    a fatter outer z (2.2) for the 5%/95% levels. Everything is clamped to
    [0, 1], monotone, rounded to 6 decimals, and deterministic: a pure
    function of the input.

Things tried and rejected on the walk-forward backtest (hourly checkpoints
over the practice window): EWMA-of-squares volatility (jump-dominated),
a jump-rate component (bands too wide), spike pullback and 6h-level
reversion for the median (no gain / hurts).

Usage: python3 main.py with MAKEFUN_INPUT_DIR / MAKEFUN_OUTPUT_DIR, or /input / /output.
Python 3.13, standard library only, no network.
"""
import json
import math
import os

INPUT_DIR = os.environ.get("MAKEFUN_INPUT_DIR", "/input")
OUTPUT_DIR = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output")

QUANTILES = [0.05, 0.25, 0.5, 0.75, 0.95]
Z_IN = 0.67449

# Tuned on the practice (walk-forward backtest + the 6 official cases).
PARAMS = {
    "win": 720,           # minutes of recent history the volatility window covers
    "win_cap": 0.02,      # winsorize |1-min moves| at this size before averaging
    "k_sigma": 1.25,      # sigma = k_sigma * winsorized mean |1-min move|
    "sigma_floor": 0.0002,
    "sigma_cap": 0.01,
    "z_out": 2.2,         # outer-quantile z (5%/95%), fatter than Gaussian
    "sum_w": 0.8,         # fraction of the cross-market sum gap closed
}


def _stats(prices, p):
    n = len(prices)
    x = prices[-1]
    # Volatility: winsorized mean |1-min move| over the recent window, scaled.
    start = max(1, n - p["win"])
    tot = 0.0
    cnt = 0
    cap = p["win_cap"]
    for i in range(start, n):
        d = abs(prices[i] - prices[i - 1])
        tot += d if d < cap else cap
        cnt += 1
    mean_abs = tot / cnt if cnt > 0 else 0.0
    sigma_eff = min(p["sigma_cap"], max(p["sigma_floor"], p["k_sigma"] * mean_abs))
    return {"x": x, "sigma_eff": sigma_eff}


def _price6(v):
    r = round(min(1.0, max(0.0, v)) * 1e6) / 1e6
    return r + 0.0  # avoid -0.0


def forecast_market(st, horizon, sum_gap, x_total, p):
    rows = []
    z_out = p["z_out"]
    # The sum adjustment is pro-rata to the market's own price: bigger markets
    # carry more of the gap between the cross-market total and 1.
    sum_share = st["x"] / x_total if x_total > 0 else 0.0
    for h in range(1, horizon + 1):
        med = st["x"] - (h / horizon) * p["sum_w"] * sum_gap * sum_share * x_total
        sd = st["sigma_eff"] * math.sqrt(h)
        row = [
            _price6(med - z_out * sd),
            _price6(med - Z_IN * sd),
            _price6(med),
            _price6(med + Z_IN * sd),
            _price6(med + z_out * sd),
        ]
        for j in range(1, 5):
            if row[j] < row[j - 1]:
                row[j] = row[j - 1]
        rows.append(row)
    return rows


def forecast_case(case, p=PARAMS):
    stats = []
    for m in case["markets"]:
        if not m["prices"]:
            raise ValueError(f"market {m['id']} has no prices")
        stats.append(_stats(m["prices"], p))
    x_total = sum(s["x"] for s in stats)
    sum_gap = x_total - 1.0
    horizon = case["horizon_minutes"]
    return [
        {"id": m["id"], "q": forecast_market(s, horizon, sum_gap, x_total, p)}
        for m, s in zip(case["markets"], stats)
    ]


def main():
    in_dir = INPUT_DIR
    out_dir = OUTPUT_DIR
    os.makedirs(out_dir, exist_ok=True)
    files = sorted(f for f in os.listdir(in_dir) if f.endswith(".in"))
    for fn in files:
        cid = fn[: -len(".in")]
        with open(os.path.join(in_dir, fn), "r", encoding="utf-8") as f:
            case = json.load(f)
        if list(case["quantiles"]) != QUANTILES:
            raise ValueError(f"{fn} asks for quantiles {case['quantiles']}")
        forecasts = forecast_case(case)
        with open(os.path.join(out_dir, f"{cid}.out"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"forecasts": forecasts}) + "\n")


if __name__ == "__main__":
    main()
