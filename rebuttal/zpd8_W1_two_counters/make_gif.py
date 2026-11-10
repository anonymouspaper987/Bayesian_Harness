"""Reviewer zpd8 W1: two-counter policy vs. Bayesian-Harness on MaskedMig (budget 24, 3 seeds).

Two counters = new runs in ./logs; the other rows are the paper's main table.
Usage: python3 rebuttal/zpd8_W1_two_counters/make_gif.py [out.gif]   (needs matplotlib + pillow)
"""
import os, sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation, PillowWriter

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "w1_two_counters.gif")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from style import INK, NAVY, BRICK, GRAY1, GRAY2   # noqa: E402
MODELS = ["Qwen3.5-4B", "Qwen3.5-9B", "Qwen3.5-27B"]
ROWS = [("Bayesian-Agent", GRAY1, "", [14.0, 18.5, 36.3], [4.8, 2.3, 7.8]),
        ("TTL", GRAY2, "", [17.8, 22.9, 40.1], [3.9, 2.3, 7.6]),
        ("Two counters (new)", BRICK, "//", [16.3, 20.4, 38.0], [1.6, 2.3, 3.3]),
        ("Bayesian-Harness (ours)", NAVY, "", [25.5, 29.9, 48.4], [1.7, 2.2, 2.8])]
GROW, GAP, HOLD = 22, 12, 30
N = GROW + GAP + HOLD
W = 0.19

fig, ax = plt.subplots(figsize=(10, 5.8), facecolor="white")
fig.subplots_adjust(left=0.085, right=0.985, top=0.83, bottom=0.08)
ease = lambda x: 1 - (1 - max(0.0, min(1.0, x))) ** 3


def draw(i):
    ax.clear()
    ax.set_ylim(0, 62); ax.set_xlim(-0.55, 2.55)
    ax.set_xticks(range(3), MODELS, fontsize=16); ax.set_ylabel("Pass rate (%)", fontsize=16)
    ax.tick_params(axis="y", labelsize=14)
    ax.yaxis.grid(True, color="#e5e5e5", lw=0.7); ax.set_axisbelow(True)
    ax.set_title("MaskedMig, budget 24, mean $\\pm$ s.d. over 3 seeds", fontsize=17, pad=10)
    f = ease(i / GROW)
    for k, (name, col, hatch, mu, sd) in enumerate(ROWS):
        xs = [j + (k - 1.5) * W for j in range(3)]
        ax.bar(xs, [m * f for m in mu], W * 0.92, color=col, hatch=hatch, edgecolor="white" if hatch else "none",
               linewidth=0.6, label=name)
        if f >= 0.999:
            ax.errorbar(xs, mu, yerr=sd, fmt="none", ecolor="#222222", elinewidth=1.1, capsize=3.5)
    for j in range(3):
        g = ease((i - GROW - 2 * j) / 8)
        if g <= 0:
            continue
        x2, x3 = j + 0.5 * W, j + 1.5 * W
        top = max(ROWS[2][3][j] + ROWS[2][4][j], ROWS[3][3][j] + ROWS[3][4][j]) + 2.5
        ax.plot([x2, x2, x3, x3], [top - 1.2, top, top, top - 1.2], color=NAVY, lw=1.3, alpha=g)
        ax.text((x2 + x3) / 2, top + 0.8, f"+{ROWS[3][3][j] - ROWS[2][3][j]:.1f}", ha="center",
                fontsize=16, fontweight="bold", color=NAVY, alpha=g)
    ax.legend(loc="upper left", ncol=4, frameon=False, fontsize=14.5, bbox_to_anchor=(-0.06, 1.22),
              handlelength=1.4, columnspacing=1.2)
    return []


FuncAnimation(fig, draw, frames=N, interval=100).save(OUT, writer=PillowWriter(fps=10), dpi=96,
                                                      savefig_kwargs={"facecolor": "white"})
print(OUT)
