"""Reviewer zpd8 W2: Oracle with / without back-off vs. Bayesian-Harness (AcmeSDK, budget 12, 3 seeds).

Reads the per-month lines of the logs in ./logs, pools the 3 seeds (pass rate = solved / exams),
writes summary.csv and w2_oracle_backoff.gif next to this file.
Usage: python3 rebuttal/zpd8_W2_oracle_backoff/make_gif.py   (needs matplotlib + pillow)
"""
import csv, os, re, statistics, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from style import INK, NAVY, BRICK, GRAY2   # noqa: E402
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation, PillowWriter

HERE = os.path.dirname(os.path.abspath(__file__))
PAT = re.compile(r"M(\d+): (\d+) 题 (\d+) 解出")
MODELS = [("q4b", "Qwen3.5-4B"), ("q9b", "Qwen3.5-9B"), ("q27b", "Qwen3.5-27B")]
ROWS = [("oracle", "Oracle (retry every period)", GRAY2, ""),
        ("oracle_backoff3", "Oracle with back-off (3 months)", BRICK, "//"),
        ("ours", "Bayesian-Harness (ours)", NAVY, "")]


def rate(path):
    n = s = 0
    for line in open(path, encoding="utf-8"):
        g = PAT.search(line)
        if g:
            n += int(g.group(2)); s += int(g.group(3))
    return s, n


MU, SD, out = {}, {}, []
for key, name, _, _ in ROWS:
    for tag, _ in MODELS:
        per = [rate(f"{HERE}/logs/acme_{tag}_s{s}_mb12_{key}.log") for s in (1, 2, 3)]
        MU[key, tag] = 100 * sum(a for a, _ in per) / sum(b for _, b in per)
        SD[key, tag] = statistics.stdev([100 * a / b for a, b in per])
        out.append([tag, key, f"{MU[key, tag]:.1f}", f"{SD[key, tag]:.1f}"])
with open(f"{HERE}/summary.csv", "w", newline="") as f:
    csv.writer(f).writerows([["model", "policy", "pass_rate_pooled", "sd_over_seeds"]] + out)

GROW, GAP, HOLD, W = 22, 12, 30, 0.25
fig, ax = plt.subplots(figsize=(10, 5.8), facecolor="white")
fig.subplots_adjust(left=0.085, right=0.985, top=0.83, bottom=0.08)
ease = lambda x: 1 - (1 - max(0.0, min(1.0, x))) ** 3


def draw(i):
    ax.clear()
    ax.set_ylim(0, 90); ax.set_xlim(-0.55, 2.55)
    ax.set_xticks(range(3), [m for _, m in MODELS], fontsize=16); ax.set_ylabel("Exam success (%)", fontsize=16)
    ax.tick_params(axis="y", labelsize=14)
    ax.yaxis.grid(True, color="#e5e5e5", lw=0.7); ax.set_axisbelow(True)
    ax.set_title("AcmeSDK, budget 12, mean $\\pm$ s.d. over 3 seeds", fontsize=17, pad=10)
    f = ease(i / GROW)
    for k, (key, name, col, hatch) in enumerate(ROWS):
        xs = [j + (k - 1) * W for j in range(3)]
        mu = [MU[key, t] for t, _ in MODELS]; sd = [SD[key, t] for t, _ in MODELS]
        ax.bar(xs, [m * f for m in mu], W * 0.92, color=col, hatch=hatch,
               edgecolor="white" if hatch else "none", linewidth=0.6, label=name)
        if f >= 0.999:
            ax.errorbar(xs, mu, yerr=sd, fmt="none", ecolor="#222222", elinewidth=1.1, capsize=3.5)
    for j, (t, _) in enumerate(MODELS):
        g = ease((i - GROW - 2 * j) / 8)
        if g <= 0:
            continue
        x0, x1 = j - W, j
        top = max(MU["oracle", t] + SD["oracle", t], MU["oracle_backoff3", t] + SD["oracle_backoff3", t]) + 2.5
        ax.plot([x0, x0, x1, x1], [top - 1.5, top, top, top - 1.5], color=BRICK, lw=1.3, alpha=g)
        ax.text((x0 + x1) / 2, top + 1.0, f"+{MU['oracle_backoff3', t] - MU['oracle', t]:.1f}", ha="center",
                fontsize=16, fontweight="bold", color=BRICK, alpha=g)
    ax.legend(loc="upper left", ncol=3, frameon=False, fontsize=14, bbox_to_anchor=(-0.06, 1.22),
              handlelength=1.4, columnspacing=1.2)
    return []


FuncAnimation(fig, draw, frames=GROW + GAP + HOLD, interval=100).save(
    f"{HERE}/w2_oracle_backoff.gif", writer=PillowWriter(fps=10), dpi=96, savefig_kwargs={"facecolor": "white"})
print(open(f"{HERE}/summary.csv").read())
