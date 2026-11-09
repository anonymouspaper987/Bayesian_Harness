"""Reviewer TvTY, W1(a) / Q2: sensitivity of Bayesian-Harness to rho, mu and eps on the reviewer's grid.

Model-free simulator (acmesdk/offline_eval.py), held-out world seed 4, the same protocol as the
81-setting sweep in the paper's appendix. Coverage = share of exams whose own note is current.

    rho in {0.1, 0.25, 0.5, 0.8}    mu in {0.25, 1, 4}    eps in {0.006, 0.012, 0.024}    kappa = 5

Usage: python3 rebuttal/TvTY_W1_release_signal/sweep_grid.py [reps=20]
Needs the seed-4 world (scripts/build_world.sh 4, or the released copy under $ACMESDK_DATA).
Writes the tables to stdout; the committed copy is sweep_grid_output.md next to this file.
"""
import collections, itertools, os, sys

if os.environ.get("PYTHONHASHSEED") != "0":        # set iteration order feeds the simulator; pin it so reruns match
    os.execvpe(sys.executable, [sys.executable] + sys.argv, {**os.environ, "PYTHONHASHSEED": "0"})

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "acmesdk"))
sys.path.insert(0, ROOT)
import offline_eval as oe                          # noqa: E402
from bayesian_harness import filters as F          # noqa: E402

reps = int(sys.argv[1]) if len(sys.argv) > 1 else 20
SEED = 4
orig = F.build_filter


def run(cond=None, **kw):
    """Coverage (%) over `reps` repetitions on world seed 4. kw overrides FilterConfig fields of ours."""
    if kw:
        def bf(method, cfg=None, **_):
            c = F.FilterConfig()
            for k, v in kw.items():
                setattr(c, k, v)
            return F.HierarchicalHMMFilter(c)
        F.build_filter = bf; oe.build_filter = bf; cond = "ours_hhmm"
    else:
        F.build_filter = orig; oe.build_filter = orig
    agg = collections.Counter()
    for r in range(reps):
        agg.update(oe.run(SEED, cond, r))
    return 100 * agg["cover"] / agg["n"]


RHO = [0.1, 0.25, 0.5, 0.8]
MU = [0.25, 1.0, 4.0]
EPS = [0.006, 0.012, 0.024]

print(f"# Sensitivity on the reviewer's grid (held-out world seed 4, {reps} repetitions, coverage %)\n")
print("| Reference | Coverage |\n|---|---|")
for c, name in (("ours_hhmm", "Bayesian-Harness, paper setting"), ("ttl", "TTL"), ("bayesian_agent", "Bayesian-Agent")):
    print(f"| {name} | {run(c):.1f} |")
print(f"| Bayesian-Harness, default rebuilt (rho 0.5, mu 1, eps 0.012, kappa 5) | "
      f"{run(hmm_rho=0.5, hmm_recover=1.0, hmm_bg=0.012, pool_kappa=5.0):.1f} |")

print("\n## rho x mu  (eps = 0.012, kappa = 5)\n")
print("| rho \\ mu | " + " | ".join(f"{m:g}" for m in MU) + " |")
print("|---|" + "---|" * len(MU))
for r in RHO:
    print(f"| {r:g} | " + " | ".join(f"{run(hmm_rho=r, hmm_recover=m, hmm_bg=0.012, pool_kappa=5.0):.1f}" for m in MU) + " |")

print("\n## eps  (rho = 0.5, mu = 1, kappa = 5)\n")
print("| eps | " + " | ".join(f"{e:g}" for e in EPS) + " |")
print("|---|" + "---|" * len(EPS))
print("| coverage | " + " | ".join(f"{run(hmm_rho=0.5, hmm_recover=1.0, hmm_bg=e, pool_kappa=5.0):.1f}" for e in EPS) + " |")

allv = sorted(run(hmm_rho=r, hmm_recover=m, hmm_bg=e, pool_kappa=5.0) for r, m, e in itertools.product(RHO, MU, EPS))
print(f"\nAll {len(allv)} settings: min {allv[0]:.1f}, median {allv[len(allv) // 2]:.1f}, max {allv[-1]:.1f}")
