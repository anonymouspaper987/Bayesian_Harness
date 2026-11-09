"""Reviewer TvTY, W1: tables and figures of this branch, rebuilt from the files next to this script.

  results/            -> summary.csv            release-aware baseline, pooled pass rate over 3 seeds +- s.d.
  results/*.maint.*   -> release_flagged.csv    how many notes the release signal flagged per period
  summary.csv         -> w1_release_signal.gif  release-aware baseline next to the paper's main-table rows
  sweep_grid_output.md-> w1_sensitivity.png     rho x mu heat map and eps row from the reviewer's grid

Usage: python3 rebuttal/TvTY_W1_release_signal/make_figures.py      (needs matplotlib + pillow)
"""
import csv, glob, json, os, re, statistics, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from style import INK, MUTE, NAVY, BRICK, GREEN, GRAY1, GRAY2   # noqa: E402
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                                 # noqa: E402
from matplotlib.animation import FuncAnimation, PillowWriter    # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = [("4b", "Qwen3.5-4B"), ("9b", "Qwen3.5-9B"), ("27b", "Qwen3.5-27B")]

# Paper rows (main tables, budget 24, 3 seeds): mean, s.d.
PAPER = {
    "maskedmig": {"Bayesian-Agent": [(14.0, 4.8), (18.5, 2.3), (36.3, 7.8)],
                  "TTL": [(17.8, 3.9), (22.9, 2.3), (40.1, 7.6)],
                  "Bayesian-Harness (ours)": [(25.5, 1.7), (29.9, 2.2), (48.4, 2.8)],
                  "Oracle": [(27.4, 1.9), (38.9, 2.5), (52.2, 1.3)]},
    "acmesdk": {"Bayesian-Agent": [(30.1, 4.2), (41.0, 5.7), (64.7, 3.8)],
                "TTL": [(36.2, 2.7), (51.2, 4.0), (62.9, 4.4)],
                "Bayesian-Harness (ours)": [(40.7, 3.8), (52.8, 7.7), (74.6, 1.4)],
                "Oracle": [(40.2, 1.7), (54.5, 7.5), (78.1, 3.2)]},
}


def pass_rate(path):
    rows = [json.loads(l) for l in open(path, encoding="utf-8")]
    return sum(1 for r in rows if r["solved"]), len(rows)


def pooled(paths):
    per = [pass_rate(p) for p in paths]
    mu = 100 * sum(s for s, _ in per) / sum(n for _, n in per)
    sd = statistics.stdev([100 * s / n for s, n in per]) if len(per) > 1 else 0.0
    return mu, sd, sum(n for _, n in per)


NEW = {"maskedmig": {}, "acmesdk": {}}
rows = [["corpus", "model", "policy", "pass_rate_pooled", "sd_over_seeds", "exams"]]
for tag, name in MODELS:
    mu, sd, n = pooled([f"{HERE}/results/maskedmig/tq1_q{tag}_s{s}_mb24_release_aware.jsonl" for s in (1, 2, 3)])
    NEW["maskedmig"][tag] = (mu, sd); rows.append(["MaskedMig", name, "release_aware (version-bump)", f"{mu:.1f}", f"{sd:.1f}", n])
    mu, sd, n = pooled([f"{HERE}/results/acmesdk/seed{s}/tq1_qwen{tag}_mb24_release_aware.jsonl" for s in (1, 2, 3)])
    NEW["acmesdk"][tag] = (mu, sd); rows.append(["AcmeSDK", name, "release_aware (changelog-aware)", f"{mu:.1f}", f"{sd:.1f}", n])
with open(f"{HERE}/summary.csv", "w", newline="") as f:
    csv.writer(f).writerows(rows)

# How much the release signal flags per period (from the maintenance logs).
flag_rows = [["corpus", "model", "seed", "periods", "release_flagged_total", "release_flagged_mean_per_period", "notes"]]
for tag, name in MODELS:
    for s in (1, 2, 3):
        for corpus, path in (("MaskedMig", f"{HERE}/results/maskedmig/tq1_q{tag}_s{s}_mb24_release_aware.maint.jsonl"),
                             ("AcmeSDK", f"{HERE}/results/acmesdk/seed{s}/tq1_qwen{tag}_mb24_release_aware.maint.jsonl")):
            ms = [json.loads(l) for l in open(path, encoding="utf-8")]
            tot = sum(m.get("release_flagged", 0) for m in ms)
            flag_rows.append([corpus, name, s, len(ms), tot, f"{tot / len(ms):.1f}", ms[-1]["n_notes"]])
with open(f"{HERE}/release_flagged.csv", "w", newline="") as f:
    csv.writer(f).writerows(flag_rows)

# ---- GIF: the release-aware baseline next to the paper's rows ---------------------------------
ORDER = [("Bayesian-Agent", GRAY1, ""), ("TTL", GRAY2, ""), ("Release-aware (new)", BRICK, "//"),
         ("Bayesian-Harness (ours)", NAVY, ""), ("Oracle", "#d9d9d9", "..")]
GROW, HOLD, W = 22, 30, 0.16
fig, axes = plt.subplots(1, 2, figsize=(13, 5.6), facecolor="white")
fig.subplots_adjust(left=0.06, right=0.99, top=0.80, bottom=0.09, wspace=0.12)
ease = lambda x: 1 - (1 - max(0.0, min(1.0, x))) ** 3


def values(corpus, key):
    if key.startswith("Release"):
        return [NEW[corpus][t] for t, _ in MODELS]
    return PAPER[corpus][key]


def draw(i):
    f = ease(i / GROW)
    for ax, (corpus, title, ymax) in zip(axes, (("maskedmig", "MaskedMig, budget 24 (real API migrations)", 62),
                                                ("acmesdk", "AcmeSDK, budget 24 (synthetic SDK)", 95))):
        ax.clear(); ax.set_ylim(0, ymax); ax.set_xlim(-0.5, 2.5)
        ax.set_xticks(range(3), [m for _, m in MODELS], fontsize=14); ax.tick_params(axis="y", labelsize=12)
        ax.yaxis.grid(True, color="#e5e5e5", lw=0.7); ax.set_axisbelow(True)
        ax.set_title(title, fontsize=14, pad=8)
        for k, (key, col, hatch) in enumerate(ORDER):
            xs = [j + (k - 2) * W for j in range(3)]
            mu = [v[0] for v in values(corpus, key)]; sd = [v[1] for v in values(corpus, key)]
            ax.bar(xs, [m * f for m in mu], W * 0.92, color=col, hatch=hatch,
                   edgecolor="white" if hatch else "none", linewidth=0.6, label=key)
            if f >= 0.999:
                ax.errorbar(xs, mu, yerr=sd, fmt="none", ecolor="#222222", elinewidth=1.0, capsize=3)
                for x, m in zip(xs, mu):
                    if key.startswith("Release") or key.startswith("Bayesian-Harness"):
                        ax.text(x, m + (sd[xs.index(x)] if sd else 0) + 1.2, f"{m:.1f}", ha="center", fontsize=10,
                                color=col if key.startswith("Release") else NAVY, fontweight="bold")
    axes[0].set_ylabel("Exam success (%), mean $\\pm$ s.d. over 3 seeds", fontsize=13)
    axes[0].legend(loc="upper left", ncol=5, frameon=False, fontsize=12, bbox_to_anchor=(-0.02, 1.26),
                   handlelength=1.3, columnspacing=1.1)
    fig.suptitle("Release-aware baseline (reads the version / changelog signal) vs. the paper's main-table rows",
                 fontsize=15, y=0.985)
    return []


FuncAnimation(fig, draw, frames=GROW + HOLD, interval=100).save(
    f"{HERE}/w1_release_signal.gif", writer=PillowWriter(fps=10), dpi=90, savefig_kwargs={"facecolor": "white"})

# ---- PNG: sensitivity on the reviewer's grid ----------------------------------------------------
txt = open(f"{HERE}/sweep_grid_output.md", encoding="utf-8").read()
ref = dict(re.findall(r"^\| (Bayesian-Harness, paper setting|TTL|Bayesian-Agent) \| ([\d.]+) \|", txt, re.M))
tbl = [l for l in txt.split("## rho x mu")[1].split("## eps")[0].splitlines() if l.startswith("| ")]
mus = [float(x) for x in re.findall(r"[\d.]+", tbl[0])]
rhos = [float(re.findall(r"[\d.]+", l)[0]) for l in tbl[1:]]
grid = [[float(x) for x in re.findall(r"[\d.]+", l)[1:]] for l in tbl[1:]]
etbl = [l for l in txt.split("## eps")[1].splitlines() if l.startswith("| ")]
eps_vals = [float(x) for x in re.findall(r"[\d.]+", etbl[0])]
eps_cov = [float(x) for x in re.findall(r"[\d.]+", etbl[1])]

fig2, (a1, a2) = plt.subplots(1, 2, figsize=(12.5, 4.8), facecolor="white", gridspec_kw={"width_ratios": [1.25, 1]})
fig2.subplots_adjust(left=0.07, right=0.98, top=0.86, bottom=0.14, wspace=0.28)
vmin, vmax = 10, 50
im = a1.imshow(grid, cmap="Blues", vmin=vmin, vmax=vmax, aspect="auto")
a1.set_xticks(range(len(mus)), [f"{m:g}" for m in mus]); a1.set_yticks(range(len(rhos)), [f"{r:g}" for r in rhos])
a1.set_xlabel(r"$\mu$  (burst recovery rate)", fontsize=13); a1.set_ylabel(r"$\rho$  (share of a subsystem hit by a burst)", fontsize=13)
a1.set_title(r"Coverage (%) over $\rho \times \mu$,  $\varepsilon$ = 0.012", fontsize=14)
for i, r in enumerate(rhos):
    for j, m in enumerate(mus):
        bold = (r == 0.5 and m == 1.0)
        a1.text(j, i, f"{grid[i][j]:.1f}", ha="center", va="center", fontsize=13,
                color="white" if grid[i][j] > 35 else INK, fontweight="bold" if bold else "normal")
a1.add_patch(plt.Rectangle((mus.index(1.0) - 0.5, rhos.index(0.5) - 0.5), 1, 1, fill=False, ec=BRICK, lw=2.2))
a1.text(mus.index(1.0), rhos.index(0.5) + 0.36, "paper setting", ha="center", fontsize=9, color=BRICK)
a2.plot(range(len(eps_vals)), eps_cov, "o-", color=NAVY, lw=2.2, ms=7, label=r"Bayesian-Harness, $\rho$ = 0.5, $\mu$ = 1")
for x, y in zip(range(len(eps_vals)), eps_cov):
    a2.text(x, y + 1.2, f"{y:.1f}", ha="center", fontsize=12, color=NAVY, fontweight="bold")
a2.axhline(float(ref["Bayesian-Agent"]), color=GRAY2, ls="--", lw=1.6)
a2.text(2.05, float(ref["Bayesian-Agent"]) + 0.8, f"Bayesian-Agent  {float(ref['Bayesian-Agent']):.1f}", ha="right", fontsize=11, color=MUTE)
a2.axhline(float(ref["TTL"]), color=GRAY2, ls=":", lw=1.6)
a2.text(2.05, float(ref["TTL"]) + 0.8, f"TTL  {float(ref['TTL']):.1f}", ha="right", fontsize=11, color=MUTE)
a2.set_xticks(range(len(eps_vals)), [f"{e:g}" for e in eps_vals]); a2.set_ylim(vmin, vmax)
a2.set_xlabel(r"$\varepsilon$  (background break rate per month)", fontsize=13); a2.set_ylabel("Coverage (%)", fontsize=13)
a2.set_title(r"Coverage over $\varepsilon$, baselines as reference lines", fontsize=14)
a2.yaxis.grid(True, color="#e5e5e5", lw=0.7); a2.set_axisbelow(True)
fig2.suptitle("Sensitivity on the reviewer's grid: held-out world (seed 4), model-free simulator, 20 repetitions", fontsize=14, y=0.985)
fig2.savefig(f"{HERE}/w1_sensitivity.png", dpi=130, facecolor="white")
print(open(f"{HERE}/summary.csv").read())
