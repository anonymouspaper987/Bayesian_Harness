"""Reviewer 5Xuf W1: animated bar chart of the MaskedMig comparison (budget 24, 3 seeds).

FactorRuleFilter numbers are the new runs in ./logs; all other rows are the paper's main table.
Usage: python3 rebuttal/W1_FactorRuleFilter/make_gif.py   (needs matplotlib + pillow)
"""
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation, PillowWriter

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = ["Qwen3.5-4B", "Qwen3.5-9B", "Qwen3.5-27B"]
ROWS = [  # name, (mean, sd) per model
    ("None", [(0.6, 0.6), (0.6, 1.1), (12.1, 0.6)]),
    ("Static", [(0.6, 0.0), (1.3, 2.2), (8.9, 8.3)]),
    ("Bayesian-Agent", [(14.0, 4.8), (18.5, 2.3), (36.3, 7.8)]),
    ("Random", [(16.6, 5.1), (21.0, 8.0), (37.6, 1.7)]),
    ("TTL", [(17.8, 3.9), (22.9, 2.3), (40.1, 7.6)]),
    ("FactorRuleFilter (new)", [(14.9, 2.0), (20.0, 2.0), (39.3, 2.9)]),
    ("Bayesian-Harness (ours)", [(25.5, 1.7), (29.9, 2.2), (48.4, 2.8)]),
    ("Oracle", [(27.4, 1.9), (38.9, 2.5), (52.2, 1.3)]),
]
COLOR = {"FactorRuleFilter (new)": "#ea580c", "Bayesian-Harness (ours)": "#2563eb", "Oracle": "#9ca3af"}
GROW, HOLD = 24, 20

plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})
fig, axes = plt.subplots(1, 3, figsize=(13, 4.8), sharey=True)
fig.suptitle("Does sharing subsystem information alone explain the gain?\n"
             "MaskedMig, budget 24, 3 seeds (pass rate %)", fontsize=12, fontweight="bold")
bars, texts = [], []
for j, ax in enumerate(axes):
    ax.set_title(MODELS[j]); ax.set_xlim(0, 66)
    ys = range(len(ROWS))
    b = ax.barh(ys, [0] * len(ROWS), color=[COLOR.get(n, "#cbd5e1") for n, _ in ROWS],
                hatch=None, edgecolor="white")
    for k, (n, _) in enumerate(ROWS):
        if n.startswith("FactorRule"):
            b[k].set_hatch("//"); b[k].set_edgecolor("#7c2d12")
    bars.append(b)
    texts.append([ax.text(0, k, "", va="center", fontsize=9) for k in ys])
    ax.set_xlabel("pass rate (%)")
axes[0].set_yticks(range(len(ROWS)), [n for n, _ in ROWS])
for lab in axes[0].get_yticklabels():
    if lab.get_text() in COLOR and lab.get_text() != "Oracle":
        lab.set_fontweight("bold"); lab.set_color(COLOR[lab.get_text()])
note = fig.text(0.5, 0.02, "", ha="center", fontsize=10, style="italic", color="#444")
fig.tight_layout(rect=(0, 0.06, 1, 0.9))


def draw(i):
    f = min(1.0, (i + 1) / GROW)
    f = 1 - (1 - f) ** 3
    for j in range(3):
        for k, (n, vals) in enumerate(ROWS):
            mu, sd = vals[j]
            bars[j][k].set_width(mu * f)
            texts[j][k].set_position((mu * f + 0.8, k))
            texts[j][k].set_text(f"{mu:.1f} ± {sd:.1f}" if f > 0.99 else "")
    if i >= GROW:
        note.set_text("FactorRuleFilter shares suspicion across a subsystem but stays near TTL; "
                      "the gain comes from modelling validity and quality.")
    return []


FuncAnimation(fig, draw, frames=GROW + HOLD, interval=80).save(
    f"{HERE}/w1_factor_rule.gif", writer=PillowWriter(fps=12), dpi=80)
