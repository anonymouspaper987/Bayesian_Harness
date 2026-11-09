# Sensitivity on the reviewer's grid (held-out world seed 4, 20 repetitions, coverage %)

| Reference | Coverage |
|---|---|
| Bayesian-Harness, paper setting | 42.7 |
| TTL | 16.7 |
| Bayesian-Agent | 29.4 |
| Bayesian-Harness, default rebuilt (rho 0.5, mu 1, eps 0.012, kappa 5) | 42.7 |

## rho x mu  (eps = 0.012, kappa = 5)

| rho \ mu | 0.25 | 1 | 4 |
|---|---|---|---|
| 0.1 | 41.9 | 40.2 | 41.4 |
| 0.25 | 42.1 | 42.6 | 41.0 |
| 0.5 | 42.4 | 42.7 | 41.5 |
| 0.8 | 41.9 | 42.0 | 40.5 |

## eps  (rho = 0.5, mu = 1, kappa = 5)

| eps | 0.006 | 0.012 | 0.024 |
|---|---|---|---|
| coverage | 42.0 | 42.7 | 43.7 |

All 36 settings: min 37.6, median 42.0, max 43.8
