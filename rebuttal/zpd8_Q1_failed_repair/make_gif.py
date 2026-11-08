"""Reviewer zpd8 Q1: animate reset vs keep-and-retry from the run logs in ./logs.

Reads the per-month lines "Mxx: N 题 S 解出, ..., probes P repairs R(ok K)" of every log,
pools the 3 seeds, and writes q1_failed_repair.gif and summary.csv next to this file.
Usage: python3 rebuttal/zpd8_Q1_failed_repair/make_gif.py   (needs matplotlib + pillow)
"""
import csv, os, re, statistics
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation, PillowWriter

HERE = os.path.dirname(os.path.abspath(__file__))
PAT = re.compile(r"M(\d+): (\d+) 题 (\d+) 解出.*probes (\d+) repairs (\d+)\(ok (\d+)\)")
MODELS = [("q4b", "Qwen3.5-4B"), ("q9b", "Qwen3.5-9B"), ("q27b", "Qwen3.5-27B")]
VARS = [("reset", "Reset after a failed repair (ours)", "#2563eb"),
        ("keep_and_retry", "Keep as broken and retry", "#ea580c")]
SEEDS = (1, 2, 3)
M = 36


def parse(path):
    rows = {}
    for line in open(path, encoding="utf-8"):
        g = PAT.search(line)
        if g:
            m, n, s, p, r, ok = map(int, g.groups())
            rows[m] = (n, s, p, r, ok)
    return rows


data, summary = {}, []
for tag, _ in MODELS:
    for v, _, _ in VARS:
        runs = [parse(f"{HERE}/logs/acme_{tag}_s{s}_mb12_{v}.log") for s in SEEDS]
        cum_rate, cum_probe, cn, cs, cp = [], [], 0, 0, 0
        for m in range(1, M + 1):
            for r in runs:
                n, s, p, _, _ = r.get(m, (0, 0, 0, 0, 0))
                cn, cs, cp = cn + n, cs + s, cp + p
            cum_rate.append(100 * cs / cn if cn else float("nan"))
            cum_probe.append(cp / len(SEEDS))
        data[tag, v] = (cum_rate, cum_probe)
        per = [100 * sum(x[1] for x in r.values()) / sum(x[0] for x in r.values()) for r in runs]
        rep = sum(sum(x[3] for x in r.values()) for r in runs) / len(SEEDS)
        summary.append([tag, v, f"{cum_rate[-1]:.1f}", f"{statistics.stdev(per):.1f}",
                        f"{cum_probe[-1]:.0f}", f"{rep:.0f}"])

with open(f"{HERE}/summary.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["model", "variant", "pass_rate_pooled", "sd_over_seeds", "probes_per_run", "repairs_per_run"])
    w.writerows(summary)

plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})
fig, axes = plt.subplots(2, 3, figsize=(12, 6.4), sharex=True)
fig.suptitle("After a failed repair: reset (ours) vs. keep as broken and retry\n"
             "AcmeSDK, budget 12 per month, 3 seeds", fontsize=12, fontweight="bold")
lines = {}
for j, (tag, name) in enumerate(MODELS):
    top, bot = axes[0, j], axes[1, j]
    top.set_title(name)
    lo = min(min(x for x in data[tag, v][0][3:] if x == x) for v, _, _ in VARS)
    hi = max(max(x for x in data[tag, v][0][3:] if x == x) for v, _, _ in VARS)
    top.set_ylim(lo - 5, hi + 5); top.set_xlim(1, M + 5); bot.set_xlim(1, M + 5)
    bot.set_ylim(0, max(data[tag, v][1][-1] for v, _, _ in VARS) * 1.1)
    bot.set_xlabel("month")
    for v, lab, c in VARS:
        lines[tag, v, 0], = top.plot([], [], color=c, lw=2.2, label=lab)
        lines[tag, v, 1], = bot.plot([], [], color=c, lw=2.2)
        lines[tag, v, 2] = top.text(0, 0, "", color=c, fontsize=9, fontweight="bold", va="center")
axes[0, 0].set_ylabel("cumulative pass rate (%)")
axes[1, 0].set_ylabel("cumulative probes per run")
fig.legend(*axes[0, 0].get_legend_handles_labels(), loc="lower center", ncol=2, frameon=False)
note = fig.text(0.5, 0.075, "", ha="center", fontsize=10, style="italic", color="#444")
fig.tight_layout(rect=(0, 0.1, 1, 0.95))

HOLD = 18


def draw(i):
    k = min(i + 1, M)
    xs = list(range(1, k + 1))
    for tag, _ in MODELS:
        for v, _, _ in VARS:
            r, p = data[tag, v]
            lines[tag, v, 0].set_data(xs[2:], r[2:k])
            lines[tag, v, 1].set_data(xs, p[:k])
            lines[tag, v, 2].set_position((min(k, M) + 0.3, r[k - 1]))
            lines[tag, v, 2].set_text(f"{r[k-1]:.1f}" if k >= 3 else "")
    if i >= M:
        note.set_text("Retrying every period spends the budget on notes the model cannot fix, "
                      "so fewer notes get probed and fewer exams pass.")
    return []


anim = FuncAnimation(fig, draw, frames=M + HOLD, interval=140)
anim.save(f"{HERE}/q1_failed_repair.gif", writer=PillowWriter(fps=7), dpi=80)
print(open(f"{HERE}/summary.csv").read())
