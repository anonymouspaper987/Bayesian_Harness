"""Rebuild summary.csv, maintenance.csv and w1c_baselines.png from the result files in this folder.
Usage: python3 rebuttal/TvTY_W1c_llm_memory_baselines/make_figure.py"""
import csv, json, os, statistics, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from style import INK, MUTE, NAVY, BRICK, GRAY1, GRAY2, PINK        # noqa: E402
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                                     # noqa: E402

TAGS = [("q4b", "Qwen3.5-4B"), ("q9b", "Qwen3.5-9B"), ("q27b", "Qwen3.5-27B")]
NEW = [("llm_rank", "LLM prioritisation"), ("mem0", "Mem0-style update"), ("amem", "A-Mem-style links")]
PAPER = {  # printed values of the paper (MaskedMig main table, budget 24, 3 seeds)
    "Bayesian-Agent (paper)": [(14.0, 4.8), (18.5, 2.3), (36.3, 7.8)],
    "TTL (paper)": [(17.8, 3.9), (22.9, 2.3), (40.1, 7.6)],
    "Bayesian-Harness (ours, paper)": [(25.5, 1.7), (29.9, 2.2), (48.4, 2.8)],
}


def load(tag, seed, cond, kind=""):
    return [json.loads(l) for l in open(f"{HERE}/results/maskedmig/tw1c_{tag}_s{seed}_mb24_{cond}{kind}.jsonl")]


rows, mrows, new = [], [], {}
for c, cn in NEW:
    new[cn] = []
    for t, tn in TAGS:
        per, st, fs = [], {"probes": 0, "probe_valid": 0, "repairs": 0, "repair_ok": 0}, {}
        for sd in (1, 2, 3):
            rs = load(t, sd, c)
            per.append(100 * sum(bool(r["solved"]) for r in rs) / len(rs))
            ms = load(t, sd, c, ".maint")
            for k in st:
                st[k] += sum(m[k] for m in ms)
            for k, v in (ms[-1]["flt_stats"] or {}).items():
                fs[k] = fs.get(k, 0) + v
        new[cn].append((statistics.mean(per), statistics.stdev(per)))
        rows.append([c, cn, tn, f"{statistics.mean(per):.1f}", f"{statistics.stdev(per):.1f}"] + [f"{x:.1f}" for x in per])
        mrows.append([c, tn, st["probes"], st["probe_valid"], st["repairs"], st["repair_ok"],
                      fs.get("llm_calls", 0), fs.get("llm_errors", 0)])
with open(f"{HERE}/summary.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["policy", "name", "model", "pass_rate", "sd_over_seeds", "seed1", "seed2", "seed3"])
    w.writerows(rows)
with open(f"{HERE}/maintenance.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["policy", "model", "probes", "probes_on_valid_notes", "repairs", "repairs_ok", "llm_calls", "llm_errors"])
    w.writerows(mrows)

series = [("Bayesian-Agent (paper)", PAPER["Bayesian-Agent (paper)"], GRAY1),
          ("TTL (paper)", PAPER["TTL (paper)"], GRAY2),
          ("LLM prioritisation (new)", new["LLM prioritisation"], PINK),
          ("Mem0-style update (new)", new["Mem0-style update"], "#F39B7F"),
          ("A-Mem-style links (new)", new["A-Mem-style links"], BRICK),
          ("Bayesian-Harness (ours, paper)", PAPER["Bayesian-Harness (ours, paper)"], NAVY)]
fig, ax = plt.subplots(figsize=(12.5, 5.4))
w = 0.135
for i, (name, vals, col) in enumerate(series):
    xs = [g + (i - 2.5) * w for g in range(3)]
    ax.bar(xs, [v for v, _ in vals], w * 0.92, yerr=[s for _, s in vals], color=col, label=name,
           error_kw={"elinewidth": 1.0, "ecolor": MUTE, "capsize": 2.5})
    for x, (v, s) in zip(xs, vals):
        ax.text(x, v + s + 0.8, f"{v:.1f}", ha="center", va="bottom", fontsize=10.5, color=INK)
ax.set_xticks(range(3))
ax.set_xticklabels([tn for _, tn in TAGS])
ax.set_ylabel("Tasks solved (%)")
ax.set_ylim(0, 60)
ax.set_title("MaskedMig, budget 24, 3 seeds", fontsize=15, color=INK, pad=10)
ax.legend(frameon=False, ncol=3, fontsize=11.5, loc="upper left")
fig.tight_layout()
fig.savefig(f"{HERE}/w1c_baselines.png", dpi=130, facecolor="white")
print("wrote summary.csv, maintenance.csv, w1c_baselines.png")
