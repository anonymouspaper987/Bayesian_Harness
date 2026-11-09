"""Reviewer TvTY, W2 / Q4 / Q6: every paired test quoted in the rebuttal, rebuilt from the result files next to this script.

Paired McNemar test over exams. Within one seed both policies sit the same exams (same task, same period), so
only exams where exactly one policy passes are informative. We count wins (ours passes, baseline fails) and
losses (baseline passes, ours fails), pool the three seeds, and report the exact two-sided binomial p-value.

  results/acmesdk/seed{1,2,3}/qwen{4b,9b,27b}_mb24_{ours_hhmm,ttl,bayesian_agent}.jsonl     paper runs, AcmeSDK budget 24
  results/maskedmig/tq1_q{4b,9b,27b}_s{1,2,3}_mb24_{ours_hhmm,ttl,bayesian_agent}.jsonl      MaskedMig budget 24, same stack
  results/acmesdk/seed{1,2,3}/qwen27b_mb24_ours_hhmm_no_{recover,error_channel}.jsonl         ablations (paper runs)
  results/acmesdk/seed{1,2,3}/qwen27b_mb12_lowq035_*.jsonl                                   low-quality-notes world (W2(a) / Q6)

Writes paired_cells.csv, paired_pooled.csv, ablation_paired.csv, lowq_summary.csv and w2_paired_tests.gif.
The renamed-drift subset needs the world truth files ($ACMESDK_DATA/seed*/truth); without them that
column is reported as "n/a".

Usage: python3 rebuttal/TvTY_W2_paired_tests/paired_rebuttal.py      (needs matplotlib + pillow for the GIF)
"""
import csv, json, math, os, statistics, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "acmesdk"))
RES = f"{HERE}/results"
MODELS = [("4b", "Qwen3.5-4B"), ("9b", "Qwen3.5-9B"), ("27b", "Qwen3.5-27B")]
BASES = [("ttl", "TTL"), ("bayesian_agent", "Bayesian-Agent")]


def binom_two_sided(k, n):
    if n == 0:
        return float("nan")
    lo = min(k, n - k)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(lo + 1)) / 2 ** n)


def fmt_p(p):
    return "n/a" if p != p else ("p<0.001" if p < 0.001 else f"p={p:.3f}")


def load(path, corpus):
    key = (lambda r: (r["task_id"], r["m"])) if corpus == "acme" else (lambda r: (r["task_id"], r["quarter"]))
    return {key(r): bool(r["solved"]) for r in map(json.loads, open(path, encoding="utf-8"))}


def renamed_keys(seed, keys):
    """Exams on renamed drift, via the world truth files (optional)."""
    try:
        from analyze import strict_class
        from analyze_robust import events
        import analyze_separability as A
        ev = events(f"{A.ACME}/seed{seed}")
    except Exception:
        return None
    return {k for k in keys if strict_class(ev, k[0].split("#")[0], k[1]) == "renamed"}


def compare(fa, fb, corpus, renamed=False):
    """fa, fb: {seed: path}. Returns wins, losses, p, per-seed list; None if renamed subset unavailable."""
    W = L = 0; per = []
    for s in sorted(fa):
        a, b = load(fa[s], corpus), load(fb[s], corpus)
        ks = set(a) & set(b)
        if renamed:
            ks = renamed_keys(s, ks)
            if ks is None:
                return None
        w = sum(1 for k in ks if a[k] and not b[k]); l = sum(1 for k in ks if b[k] and not a[k])
        W += w; L += l; per.append((s, w, l))
    return W, L, binom_two_sided(W, W + L), per


def acme(tag, mb, cond):
    return {s: f"{RES}/acmesdk/seed{s}/qwen{tag}_mb{mb}_{cond}.jsonl" for s in (1, 2, 3)}


def mig(tag, cond):
    return {s: f"{RES}/maskedmig/tq1_q{tag}_s{s}_mb24_{cond}.jsonl" for s in (1, 2, 3)}


# ---- per-cell paired tests (Table 1 = MaskedMig, Table 6 = AcmeSDK, budget 24) ------------------
cells = {}
rows = [["corpus", "model", "baseline", "wins", "losses", "p_two_sided", "per_seed_wins_losses"]]
for corpus, name in (("acme", "AcmeSDK"), ("mig", "MaskedMig")):
    for tag, mname in MODELS:
        for b, bname in BASES:
            fa = acme(tag, 24, "ours_hhmm") if corpus == "acme" else mig(tag, "ours_hhmm")
            fb = acme(tag, 24, b) if corpus == "acme" else mig(tag, b)
            W, L, p, per = compare(fa, fb, corpus)
            cells[name, mname, bname] = (W, L, p)
            rows.append([name, mname, bname, W, L, f"{p:.4g}", " ".join(f"s{s}:{w}/{l}" for s, w, l in per)])
with open(f"{HERE}/paired_cells.csv", "w", newline="") as f:
    csv.writer(f).writerows(rows)

pooled = [["corpus", "baseline", "wins", "losses", "p_two_sided"]]
for name in ("AcmeSDK", "MaskedMig"):
    for _, bname in BASES:
        W = sum(cells[name, m, bname][0] for _, m in MODELS); L = sum(cells[name, m, bname][1] for _, m in MODELS)
        pooled.append([name, bname, W, L, f"{binom_two_sided(W, W + L):.3g}"])
with open(f"{HERE}/paired_pooled.csv", "w", newline="") as f:
    csv.writer(f).writerows(pooled)

# ---- ablations (Qwen3.5-27B, budget 24) ----------------------------------------------------------
abl = [["ablation", "subset", "wins_full_model", "losses_full_model", "p_two_sided"]]
for cond, label in (("ours_hhmm_no_recover", "no burst recovery"), ("ours_hhmm_no_error_channel", "no error channel")):
    for renamed in (False, True):
        r = compare(acme("27b", 24, "ours_hhmm"), acme("27b", 24, cond), "acme", renamed=renamed)
        abl.append([label, "renamed drift" if renamed else "all exams"] + (["n/a"] * 3 if r is None else [r[0], r[1], f"{r[2]:.3g}"]))
with open(f"{HERE}/ablation_paired.csv", "w", newline="") as f:
    csv.writer(f).writerows(abl)

# ---- low-quality-notes world (Qwen3.5-27B, budget 12, 35% of notes valid but poor) ---------------
def pooled_rate(paths):
    per = []; S = N = 0
    for p in paths.values():
        rs = [json.loads(l) for l in open(p, encoding="utf-8")]
        k = sum(1 for r in rs if r["solved"]); per.append(100 * k / len(rs)); S += k; N += len(rs)
    return 100 * S / N, statistics.stdev(per), N


lowq = [["policy", "pass_rate_pooled", "sd_over_seeds", "exams", "wins_vs_ours", "losses_vs_ours", "p_two_sided"]]
LQ = [("ours_hhmm", "Bayesian-Harness (quality estimated)"),
      ("pbar1.0_ours_hhmm_validity_only", "Quality frozen at 1 (no quality variable)"),
      ("bayesian_agent", "Bayesian-Agent")]
fo = {s: f"{RES}/acmesdk/seed{s}/qwen27b_mb12_lowq035_ours_hhmm.jsonl" for s in (1, 2, 3)}
for cond, label in LQ:
    fb = {s: f"{RES}/acmesdk/seed{s}/qwen27b_mb12_lowq035_{cond}.jsonl" for s in (1, 2, 3)}
    mu, sd, n = pooled_rate(fb)
    if cond == "ours_hhmm":
        lowq.append([label, f"{mu:.1f}", f"{sd:.1f}", n, "", "", ""])
    else:
        W, L, p, _ = compare(fo, fb, "acme")
        lowq.append([label, f"{mu:.1f}", f"{sd:.1f}", n, W, L, f"{p:.3g}"])
with open(f"{HERE}/lowq_summary.csv", "w", newline="") as f:
    csv.writer(f).writerows(lowq)

for name, out in (("paired_cells.csv", rows), ("paired_pooled.csv", pooled), ("ablation_paired.csv", abl), ("lowq_summary.csv", lowq)):
    print(f"== {name}")
    for r in out:
        print(",".join(map(str, r)))

# ---- GIF ----------------------------------------------------------------------------------------
try:
    sys.path.insert(0, HERE)
    from style import INK, MUTE, NAVY, BRICK, GRAY1, GRAY2
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.animation import FuncAnimation, PillowWriter
except ImportError:
    print("matplotlib / pillow not available, GIF skipped"); sys.exit(0)

GROW, HOLD, W = 20, 32, 0.19
fig, axes = plt.subplots(1, 2, figsize=(13, 5.6), facecolor="white", gridspec_kw={"width_ratios": [1, 1]})
fig.subplots_adjust(left=0.065, right=0.99, top=0.80, bottom=0.10, wspace=0.14)
ease = lambda x: 1 - (1 - max(0.0, min(1.0, x))) ** 3
GROUPS = [(m, b) for _, m in MODELS for _, b in BASES]       # 6 groups per corpus


def draw(i):
    f = ease(i / GROW)
    for ax, name, ymax in ((axes[0], "AcmeSDK", 205), (axes[1], "MaskedMig", 75)):
        ax.clear(); ax.set_ylim(0, ymax); ax.set_xlim(-0.6, len(GROUPS) - 0.4)
        ax.set_xticks(range(len(GROUPS)), [f"{m.replace('Qwen3.5-', '')}\nvs {b.replace('Bayesian-Agent', 'B-Agent')}" for m, b in GROUPS], fontsize=11)
        ax.tick_params(axis="y", labelsize=12); ax.yaxis.grid(True, color="#e5e5e5", lw=0.7); ax.set_axisbelow(True)
        ax.set_title(f"{name}, budget 24, three seeds pooled", fontsize=14, pad=8)
        for g, (m, b) in enumerate(GROUPS):
            Wn, Ln, p = cells[name, m, b]
            ax.bar(g - W / 2 - 0.01, Wn * f, W, color=NAVY, label="exams only Bayesian-Harness passes" if g == 0 else None)
            ax.bar(g + W / 2 + 0.01, Ln * f, W, color=GRAY2, label="exams only the baseline passes" if g == 0 else None)
            if f >= 0.999:
                ax.text(g - W / 2 - 0.01, Wn + ymax * 0.012, str(Wn), ha="center", fontsize=11, color=NAVY, fontweight="bold")
                ax.text(g + W / 2 + 0.01, Ln + ymax * 0.012, str(Ln), ha="center", fontsize=11, color=MUTE)
                ax.text(g, max(Wn, Ln) + ymax * 0.075, fmt_p(p), ha="center", fontsize=10.5,
                        color=BRICK if p < 0.05 else MUTE, fontweight="bold" if p < 0.05 else "normal")
    axes[0].set_ylabel("Discordant exams (count)", fontsize=13)
    axes[0].legend(loc="upper left", ncol=2, frameon=False, fontsize=12, bbox_to_anchor=(-0.02, 1.22))
    fig.suptitle("Paired McNemar tests: Bayesian-Harness vs. TTL and Bayesian-Agent, exact two-sided p (red = p < 0.05)",
                 fontsize=14.5, y=0.985)
    return []


FuncAnimation(fig, draw, frames=GROW + HOLD, interval=100).save(
    f"{HERE}/w2_paired_tests.gif", writer=PillowWriter(fps=10), dpi=90, savefig_kwargs={"facecolor": "white"})
print("wrote w2_paired_tests.gif")
