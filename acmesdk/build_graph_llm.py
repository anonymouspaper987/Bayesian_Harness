"""LLM 建图: 读每张笔记 (调用 + 输出), 推断它依赖的"共用实现"类别与强度, 输出 note_id -> [[factor, weight], ...].
两步: (1) 每张笔记 -> 从固定词表挑一个行为家族 + 置信度 (0-1); 词表由第 0 步让模型从 60 张抽样笔记里归纳 (<=15 个家族).
用法: python build_graph_llm.py --seed 1 --port 8000 --model Qwen/Qwen3.5-27B --out graphs/seed1_llm.json"""
import argparse, json, os, re, sys, random, concurrent.futures as cf
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "maskedmig"))
from sworld import seed_dir
import llm_backend

def chat(port, model, msgs, max_tokens=600):
    import llm
    return llm.chat(port, model, msgs, max_tokens=max_tokens, temperature=0.0)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=1); ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--model", default="Qwen/Qwen3.5-27B"); ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=8); ap.add_argument("--n-families", type=int, default=15)
    a = ap.parse_args()
    print("后端:", llm_backend.install(a.model), flush=True)
    notes = [json.loads(l) for l in open(f"{seed_dir(a.seed)}/notes_M01.jsonl")]
    rng = random.Random(7); sample = rng.sample(notes, min(60, len(notes)))
    # 第 0 步: 归纳家族词表
    ex = "\n\n".join(f"[{i+1}] {n['snippet'].strip()}" for i, n in enumerate(sample))
    vocab_msg = [{"role": "system", "content": "You are a senior engineer reverse-engineering a company SDK from usage examples. You care about HOW functions compute, not what business domain they serve."},
                 {"role": "user", "content": f"Below are {len(sample)} verified usage snippets of functions from an internal SDK. "
                  f"Functions that share the same underlying implementation break together when that implementation changes. "
                  f"IGNORE the module name and the business meaning of each function (auth, billing, search ...). Look only at the input types, the arithmetic or string/list operation performed, "
                  f"and the output type/convention (plain number, rounded value, tier index, formatted string, joined string, top-k list, aggregated total, shifted date, picked field, clamped value ...).\n"
                  f"Group the snippets into at most {a.n_families} implementation families defined purely by computation. For each family give a short snake_case id naming the computation and a one-line description of inputs -> output.\n"
                  f"Return ONLY a JSON list of objects {{\"id\": ..., \"desc\": ...}}.\n\n{ex}"}]
    txt = chat(a.port, a.model, vocab_msg, max_tokens=1500)
    try:
        fams = json.loads(txt[txt.index("["):txt.rindex("]") + 1])
    except Exception:                                                # noqa: BLE001
        print("词表解析失败, 原始回复:", txt[:800]); fams = []
    fam_ids = [f["id"] for f in fams if "id" in f]
    print("家族词表:", fam_ids, flush=True)
    vocab_txt = "\n".join(f"- {f['id']}: {f.get('desc','')}" for f in fams)
    # 第 1 步: 每张笔记归类 + 置信度
    def label(n):
        msg = [{"role": "system", "content": "You classify SDK functions by the shared implementation family they most likely rely on. Ignore module names and business meaning; judge by input types, the operation performed and the output convention."},
               {"role": "user", "content": f"Implementation families:\n{vocab_txt}\n\nUsage snippet:\n{n['snippet'].strip()}\n\n"
                f"Which family does this function most likely rely on, and how confident are you (0 to 1)? Optionally a second family with its own confidence if the function plausibly relies on two. "
                f"Return ONLY JSON: {{\"primary\": id, \"p\": number, \"secondary\": id or null, \"p2\": number or null}}"}]
        try:
            t = chat(a.port, a.model, msg, max_tokens=200)
            mo = re.search(r"\{.*\}", t, re.S); d = json.loads(mo.group(0)) if mo else {}
        except Exception as e:                                       # noqa: BLE001
            d = {}
        out = []
        if d.get("primary") in fam_ids: out.append([f"llm:{d['primary']}", max(0.0, min(1.0, float(d.get("p") or 0.5)))])
        if d.get("secondary") in fam_ids and d.get("p2"): out.append([f"llm:{d['secondary']}", max(0.0, min(1.0, float(d["p2"])))])
        return n["note_id"], out
    graph = {}
    with cf.ThreadPoolExecutor(a.workers) as ex_:
        for nid, out in ex_.map(label, notes):
            graph[nid] = out
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    json.dump({"families": fams, "graph": graph}, open(a.out, "w"), indent=1)
    import collections
    print("归类分布:", collections.Counter(g[0][0] for g in graph.values() if g).most_common(20))
    print("无归类:", sum(1 for g in graph.values() if not g), "写出", a.out, flush=True)

if __name__ == "__main__":
    main()
