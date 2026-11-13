# nvidia-ceo-forecast

Entry for make.fun's `polymarket-nvidia-ceo-forecast-01`: forecast the Yes price
of every trading market of Polymarket's "Next CEO of NVIDIA?" event at every
minute of the 4 hours after a checkpoint, as five quantiles (5%, 25%, 50%, 75%,
95%), for the lowest mean pinball loss.

## How it works

`main.py` (Python 3.13, standard library only, no network) reads every
`<id>.in` in `$MAKEFUN_INPUT_DIR` (else `/input`) and writes `<id>.out` to
`$MAKEFUN_OUTPUT_DIR` (else `/output`). One run per case, deterministic.

Per market, from its own 72 hours of 1-minute Yes prices:

- **Median:** the last price. A spike in the very last minute is pulled back a
  little (1-minute spikes in this event revert fast), and the medians are eased
  toward a cross-market total of 1 — the 24 outcomes are mutually exclusive,
  so when the Yes prices sum above 1 they tend to come down, and vice versa.
- **Spread:** a per-market sigma from the *winsorized* mean |1-minute move|
  over the last 12 hours, scaled. Winsorizing (not RMS) is the key choice:
  these prices are mostly flat with rare enormous jumps (a market listing at
  0.48 and collapsing), and an RMS/EWMA of squared moves lets one jump inflate
  the band for hours. Quantiles are `median + z_tau * sigma * sqrt(h)` with a
  fatter outer z (2.2) for the 5%/95% levels, clamped to [0, 1], monotone,
  rounded to 6 decimals.

## Validation (practice cases p00–p05, scored against Polymarket history)

| | loss | median error | 90% coverage |
|---|---:|---:|---:|
| House baseline | 0.363589 | 0.767503 | 0.949642 |
| **This entry** | **0.266697** | **0.746485** | **0.898047** |

Better than the baseline on every one of the six practice cases
(p00 0.427 vs 0.536, p01 0.310 vs 0.380, p02 0.339 vs 0.408, p03 0.257 vs 0.369,
p04 0.204 vs 0.299, p05 0.119 vs 0.238). Also tuned on a walk-forward backtest
(hourly checkpoints over the practice window): 0.294 vs the baseline's 0.389.

- Determinism: two runs on all six cases are byte-identical.
- Runtime: ~0.33 s for all six cases together (~0.05 s per case; cap is 8 s).
- Output size: 223–289 KB per case (cap is 1 MiB). All cases valid.

## Run locally

```sh
mkdir -p out
MAKEFUN_INPUT_DIR=<kit>/input MAKEFUN_OUTPUT_DIR=out python3 main.py
```

## License

MIT (see LICENSE).
