"""nvidia-ceo-forecast: Polymarket "Next CEO of NVIDIA?" 4-hour-ahead quantile forecaster.

Model (per market, from its own 72h of 1-minute Yes prices):
  * median: the last price, with three adjustments:
      - jump bounce: a big 1-minute move (|d| > 0.02) is pulled back by 80%
        of the move, ramped in linearly over the first 120 minutes of the
        horizon. Measured on the practice history, 1-minute jumps revert
        (slope about -0.4 on the mean move, more on the median); the ramp
        reflects that the give-back takes tens of minutes, not one.
      - sum easing: the medians are eased toward a cross-market total of 1
        (the 24 outcomes are mutually exclusive, so when the Yes prices sum
        above 1 they tend to come down, and vice versa), phased in over the
        horizon. The gap is allocated in proportion to each market's price
        SQUARED, so the big markets carry most of it.
  * spread: a per-market sigma from the winsorized mean |1-minute move| over
    the last 12 hours, scaled. Winsorizing (not RMS) is the key choice: these
    prices are mostly flat with rare enormous jumps (a market listing at 0.48
    and collapsing), and an RMS/EWMA of squared moves lets one jump inflate
    the band for hours. Quantiles are median +/- z_tau * sigma * sqrt(240) *
    (h/240)**alpha, with a fatter outer z (2.2) for the 5%/95% levels and
    narrower inner z (0.6 down, 0.5 up) for the 25%/75% levels: the inner
    bands were systematically too wide at the Gaussian 0.67449, and narrowing
    them (more on the up side, where jumps revert harder) gains on both
    practice and backtest. The down-side h-exponent is 0.60, the up-side
    0.65: the bands grow faster than sqrt(h) at short horizons is wrong here
    -- the empirical h-step spread grows more slowly than sqrt(h) at short h
    and the 0.60/0.65 exponents fit the practice window better. Everything is
    clamped to [0, 1], monotone, rounded to 6 decimals, and deterministic: a
    pure function of the input.

Things tried and rejected (practice loss + walk-forward backtest, both had
to improve): EWMA-of-squares volatility (jump-dominated), a jump-rate
component (bands too wide), spike pullback toward a 30-60 minute anchor
(no gain / hurts -- only the last 1-minute jump carries signal), 6h-level
reversion and shrinkage toward the field mean for the median (hurts),
level-dependent median drift (hurts), asymmetric up/down quantile z
(hurts in the round-4 combinations tried -- except the narrower inner-z
split, 0.6 down / 0.5 up, that entry 4 adopts, which gains on both
practice and backtest), signed down/up volatility (hurts), per-market empirical h-step
quantiles (much worse -- the tail estimates are too noisy and regime-bound),
and momentum terms (hurts badly).

Usage: python3 main.py with MAKEFUN_INPUT_DIR / MAKEFUN_OUTPUT_DIR, or /input / /output.
Python 3.13, standard library only, no network.
"""
import json
import math
import os

INPUT_DIR = os.environ.get("MAKEFUN_INPUT_DIR", "/input")
OUTPUT_DIR = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output")

QUANTILES = [0.05, 0.25, 0.5, 0.75, 0.95]

# Tuned on the practice cases + the walk-forward backtest (hourly checkpoints
# over the practice window). Practice loss 0.251760, backtest 0.282383
# (house baseline 0.363589 / 0.3895). Entry 4 change vs entry 3: narrower
# inner quantiles (z_in 0.67449 -> 0.6 down / 0.5 up), down-side h-exponent
# 0.65 -> 0.60, jump bounce 0.8 -> 0.85. The inner-z narrowing alone gains
# on both practice and backtest across the whole 0.4-0.6 range (a systematic
# effect, not a single-knob fluke); the combined variant is better than
# entry 3 on ALL 6 practice cases and on 19/21 backtest checkpoints (worst
# single checkpoint +0.0012, best -0.0052).
PARAMS = {
    "win": 720,           # minutes of recent history the volatility window covers
    "win_cap": 0.02,      # winsorize |1-min moves| at this size before averaging
    "k_sigma": 1.25,      # sigma = k_sigma * winsorized mean |1-min move|
    "sigma_floor": 0.0002,
    "sigma_cap": 0.005,
    "z_out": 2.2,         # outer-quantile z (5%/95%), fatter than Gaussian
    "z_in_d": 0.6,        # inner-quantile z, down side (25%)
    "z_in_u": 0.5,        # inner-quantile z, up side (75%)
    "sum_w": 0.8,         # fraction of the cross-market sum gap closed
    "sum_pow": 2.0,       # sum gap allocated in proportion to price**sum_pow
    "alpha_d": 0.60,      # h-exponent of the down-side quantile offsets
    "alpha_u": 0.65,      # h-exponent of the up-side quantile offsets
    "jump_bounce": 0.85,  # fraction of a big 1-min jump pulled back
    "jump_thr": 0.02,     # only bounce 1-min jumps bigger than this
    "jump_phase": 120,    # minutes to ramp the bounce in over
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
    jump = (x - prices[n - 2]) if n >= 2 else 0.0
    if abs(jump) <= p["jump_thr"]:
        jump = 0.0
    return {"x": x, "sigma_eff": sigma_eff, "jump": jump}


def _price6(v):
    r = round(min(1.0, max(0.0, v)) * 1e6) / 1e6
    return r + 0.0  # avoid -0.0


def forecast_market(st, horizon, sum_gap, x_total, pow_total, p):
    rows = []
    z_out = p["z_out"]
    sq240 = math.sqrt(240.0)
    # The sum adjustment is allocated in proportion to price**sum_pow: bigger
    # markets carry more of the gap between the cross-market total and 1.
    sum_share = (st["x"] ** p["sum_pow"]) / pow_total if pow_total > 0 else 0.0
    jump = st["jump"]
    jb = p["jump_bounce"]
    phase = p["jump_phase"]
    for h in range(1, horizon + 1):
        med = st["x"] - (h / horizon) * p["sum_w"] * sum_gap * sum_share * x_total
        if jump:
            med -= jb * jump * min(1.0, h / phase)
        sd_d = st["sigma_eff"] * sq240 * (h / 240.0) ** p["alpha_d"]
        sd_u = st["sigma_eff"] * sq240 * (h / 240.0) ** p["alpha_u"]
        row = [
            _price6(med - z_out * sd_d),
            _price6(med - p["z_in_d"] * sd_d),
            _price6(med),
            _price6(med + p["z_in_u"] * sd_u),
            _price6(med + z_out * sd_u),
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
    pow_total = sum(s["x"] ** p["sum_pow"] for s in stats)
    horizon = case["horizon_minutes"]
    return [
        {"id": m["id"], "q": forecast_market(s, horizon, sum_gap, x_total, pow_total, p)}
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
