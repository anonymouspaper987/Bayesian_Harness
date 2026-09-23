"""逐题配对检验 (McNemar 精确检验): 同一种子里两个策略考的是同一批题, 只看两者结果不同的题 (一对一错),
在三个种子里把这些题数加起来, 用二项分布精确算双侧 p 值. 这是三种子设定下唯一有功效的检验.
用法:
  python3 paired_tests.py --corpus acme --tag qwen27b --mb 24 --seeds 1,2,3            # Table 2 一格: ours vs ttl/ba/random, renamed + all
  python3 paired_tests.py --corpus acme --table2 --latex                               # Table 2 全部 9 格 (三模型 x 三预算)
  python3 paired_tests.py --corpus acme --sep --mb 12 --mode name --seeds 1,2,3,4,5    # Table 4 一块: ours vs 其余四行
  python3 paired_tests.py --corpus mig --sep --mode public --seeds 1,2,3               # Table 4 MaskedMig 块
"""
import argparse, json, math, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from analyze import strict_class                                     # noqa: E402
from analyze_robust import events                                    # noqa: E402
import analyze_separability as A                                     # noqa: E402

ACME = A.ACME; MIG = A.MIG


def binom_two_sided(k, n):
    """双侧精确二项检验 p 值, p0 = 0.5."""
    if n == 0:
        return float("nan")
    def pmf(i):
        return math.comb(n, i) * 0.5 ** n
    lo = min(k, n - k)
    p = sum(pmf(i) for i in range(0, lo + 1)) * 2
    return min(1.0, p)


def load(f, key):
    rows = [json.loads(l) for l in open(f)]
    return {key(r): bool(r["solved"]) for r in rows}


def key_acme(r):
    return (r["task_id"], r["m"])


def key_mig(r):
    return (r["task_id"], r["quarter"])


def compare(files_a, files_b, corpus, subset=None):
    """files_a/b: {seed: path}. subset: None | 'renamed' (acme). 返回 (wins, losses, n_paired, p, per_seed)."""
    W = L = N = 0; per = []
    for s in sorted(files_a):
        if s not in files_b or not (os.path.exists(files_a[s]) and os.path.exists(files_b[s])):
            continue
        key = key_acme if corpus == "acme" else key_mig
        a = load(files_a[s], key); b = load(files_b[s], key)
        ks = set(a) & set(b)
        if subset == "renamed":
            ev = events(f"{ACME}/seed{s}")
            ks = {k for k in ks if strict_class(ev, k[0].split("#")[0], k[1]) == "renamed"}
        w = sum(1 for k in ks if a[k] and not b[k]); l = sum(1 for k in ks if b[k] and not a[k])
        W += w; L += l; N += len(ks); per.append((s, w, l))
    return W, L, N, binom_two_sided(W, W + L), per


def fmt_p(p):
    if p != p:
        return "--"
    if p < 0.001:
        return "$<$0.001"
    return f"{p:.3f}"


def table2(tags, mbs, seeds, latex):
    print(f"{'model':<9}{'budget':>7}  {'vs TTL (W/L, p)':<26}{'vs Bayesian-Agent':<26}{'vs Random':<26}   [renamed: vs TTL | vs BA]")
    tex = []
    names = {"qwen4b": "Qwen3.5-4B", "qwen9b": "Qwen3.5-9B", "qwen27b": "Qwen3.5-27B", "g31pro": "Gemini 3.1 Pro", "main": "Gemini 2.5 Flash-Lite"}
    for tag in tags:
        for mb in mbs:
            fa = {s: f"{ACME}/seed{s}/results/{tag}_mb{mb}_ours_hhmm.jsonl" for s in seeds}
            cells = []; ren = []
            for base in ("ttl", "bayesian_agent", "random"):
                fb = {s: f"{ACME}/seed{s}/results/{tag}_mb{mb}_{base}.jsonl" for s in seeds}
                W, L, N, p, per = compare(fa, fb, "acme")
                cells.append((W, L, p, N))
                if base != "random":
                    Wr, Lr, Nr, pr, _ = compare(fa, fb, "acme", subset="renamed")
                    ren.append((Wr, Lr, pr))
            if all(c[3] == 0 for c in cells):
                print(f"{tag:<9}{mb:>7}  (no files)"); continue
            def plain(x):
                return fmt_p(x).replace("$<$", "<")
            row = f"{tag:<9}{mb:>7}  " + "".join((str(c[0]) + "/" + str(c[1]) + ", p=" + plain(c[2])).ljust(26) for c in cells)
            row += "   [" + " | ".join(str(r[0]) + "/" + str(r[1]) + " p=" + plain(r[2]) for r in ren) + f"]  n={cells[0][3]}"
            print(row)
            tex.append(f"{names.get(tag, tag)} & {mb} & " + " & ".join(f"{c[0]} / {c[1]} & {fmt_p(c[2])}" for c in cells) + "\\\\")
    if latex:
        print("\n-- LaTeX rows (model & budget & W/L & p  x3) --")
        for r in tex:
            print(r)


def sep(corpus, tag, mb, mode, seeds, latex):
    """Table 4: ours vs 其余四行, all exams (+ renamed for acme)."""
    ours = {s: A.path(corpus, tag, mb, mode, s, "", "ours_hhmm") for s in seeds}
    others = [("Bayesian-Agent (single score)", "", "bayesian_agent"), ("Two counters", "", "two_counters"),
              ("quality frozen", "pbar1_", "ours_hhmm_validity_only"), ("+ rewrite on low quality", "rw_", "ours_hhmm")]
    print(f"Table 4 {corpus} {tag} b{mb} mode {mode} seeds {seeds}: ours vs each row (W/L = exams only ours passes / only the other passes)")
    tex = []
    for lab, pfx, cond in others:
        fb = {s: A.path(corpus, tag, mb, mode, s, pfx, cond) for s in seeds}
        W, L, N, p, per = compare(ours, fb, corpus)
        line = f"  vs {lab:<32} all: {W}/{L} p=" + fmt_p(p).replace("$<$", "<") + f" (n={N})"
        cell_t = f"{W} / {L} & {fmt_p(p)}"
        if corpus == "acme":
            Wr, Lr, Nr, pr, _ = compare(ours, fb, corpus, subset="renamed")
            line += f"   renamed: {Wr}/{Lr} p=" + fmt_p(pr).replace("$<$", "<") + f" (n={Nr})"
            cell_t = f"{Wr} / {Lr} & {fmt_p(pr)} & " + cell_t
        print(line + f"   per-seed W/L {[(s, w, l) for s, w, l in per]}")
        tex.append(f"{lab} & {cell_t}\\\\")
    if latex:
        print("-- LaTeX --"); [print(r) for r in tex]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="acme"); ap.add_argument("--tag", default="qwen27b")
    ap.add_argument("--mb", type=int, default=24); ap.add_argument("--seeds", default="1,2,3")
    ap.add_argument("--mode", default="name"); ap.add_argument("--table2", action="store_true")
    ap.add_argument("--sep", action="store_true"); ap.add_argument("--latex", action="store_true")
    a = ap.parse_args(); seeds = [int(s) for s in a.seeds.split(",") if s]
    if a.table2:
        table2(["qwen4b", "qwen9b", "qwen27b", "main", "g31pro"], [12, 24, 48], seeds, a.latex)
    elif a.sep:
        sep(a.corpus, a.tag, a.mb, a.mode, seeds, a.latex)
    else:
        table2([a.tag], [a.mb], seeds, a.latex)


if __name__ == "__main__":
    main()
