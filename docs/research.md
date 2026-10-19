# OU / Zeta experiment

This optional experiment does not affect orders.

```sh
python3 main.py reversion
```

The module reads up to 300 BTC/ETH 15-minute candles. AR(1) is fitted to the preceding 120 log closes; the scoring candle is excluded from training. Requiring phi in (0,1) is not statistical proof of stationarity. Stationary SD = residual SD / sqrt(1−phi²); Zeta multiplies that Z-score by (1−phi)/(1+phi). Hurst R/S uses training log returns at scales of 8–40 bars. Thresholds |Zeta| > 1.5 and H < 0.45 remain hypotheses.

Forward diagnostics use a four-bar horizon, non-overlapping candidates, and assumed costs of 0.2%. They do not simulate fills, intrabar stops, leverage, funding, or portfolio interactions. Short samples cannot establish profitability.

References:

- [AR(1), Penn State](https://online.stat.psu.edu/stat510/Lesson01)
- [Hurst estimation bias](https://agupubs.onlinelibrary.wiley.com/doi/full/10.1029/2006WR005111)
- [OKX API](https://www.okx.com/docs-v5/)
