"""候选过滤 v2: 吸收 2026-09-04 人工核验的六类剔除理由.

新增规则 (相对 v1):
  R1 泄漏检测   __module__ 与 API 所在路径互不为前缀 -> 泄漏的 import, 剔
  R3 类级去派生 同版本对里类本身已消失 -> 其方法条目全剔
  R4 基础设施   setup_module/teardown_module/demo/test_* -> 剔
  R6 内部路径   __module__ 含 ._ / _compat / .compat 段 -> 剔
  (R2 真实调用与 R5 环境假阳性在生成/验证阶段处理)
末尾用人工核验的 60 行判例做回归: 报告规则对"剔除"判例的召回与对
"合格"判例的误杀。
"""
import collections
import json
import os
import re

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GC = os.environ.get("GITCHAMELEON_DIR") or os.path.join(_REPO, "data", "gitchameleon")
SRC = os.path.join(_GC, "migrations_inventory.jsonl")        # v2 (含 mod=)
OUT = os.path.join(_GC, "migrations_candidates_v2.jsonl")

BUILTIN_METHODS = set(dir(str)) | set(dir(dict)) | set(dir(list)) | set(dir(int))
INFRA = {"setup_module", "teardown_module", "demo"}


def split_sig(s):
    if s and "|mod=" in s:
        base, mod = s.rsplit("|mod=", 1)
        return base, mod
    return s, ""


def module_prefix(api):
    parts = api.split(".")
    # a.b.Class.method -> a.b ; a.b.func -> a.b ; a.Class -> a
    if len(parts) >= 2 and parts[-2][:1].isupper():
        return ".".join(parts[:-2])
    return ".".join(parts[:-1])


def leaked(api, mod):
    if not mod:
        return False                     # 无信息不判
    if mod in ("builtins",) or "." not in mod and not api.startswith(mod):
        return True
    pre = module_prefix(api)
    return not (mod.startswith(pre) or pre.startswith(mod))


def internal_mod(mod):
    return bool(re.search(r"(^|\.)_|_compat|\.compat", mod or ""))


def norm_params(sig):
    sig = re.sub(r"\s*->.*$", "", sig or "").strip()
    if sig.startswith("class"):
        sig = sig[5:]
    if not (sig.startswith("(") and sig.endswith(")")):
        return None
    inner, parts, depth, cur = sig[1:-1], [], 0, ""
    for ch in inner:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur); cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur)
    out = []
    for prt in parts:
        prt = prt.strip()
        name = prt.split(":")[0].split("=")[0].strip()
        default = prt.rsplit("=", 1)[1].strip() if "=" in prt else None
        out.append((name, default))
    return tuple(out)


def classify(r, removed_set):
    """返回 None=保留, 否则剔除原因"""
    api = r["api"]
    parts = api.split(".")
    old_base, old_mod = split_sig(r.get("old_sig") or "")
    if any(p.startswith("_") and p != "__init__" for p in parts):
        return "私有段"
    if len(parts) > 4:
        return "路径过深"
    if len(parts) >= 2 and parts[0] == parts[1]:
        return "重复前缀"
    if parts[-1] in INFRA or parts[-1].startswith("test_"):
        return "基础设施"
    if parts[-1] in BUILTIN_METHODS and old_base in ("(self, /)", "()", "?"):
        return "内建方法假影"
    if leaked(api, old_mod):
        return "泄漏import"
    if internal_mod(old_mod):
        return "内部模块"
    # 类级去派生: a.b.Class.method 且 a.b.Class 同对已消失
    if len(parts) >= 3 and parts[-2][:1].isupper():
        cls = ".".join(parts[:-1])
        if (r["lib"], r["old_ver"], r["new_ver"], cls) in removed_set:
            return "类级派生"
    if r["kind"] == "sig_diff":
        n0, _ = split_sig(r["old_sig"])
        n1, _ = split_sig(r["new_sig"])
        p0, p1 = norm_params(n0), norm_params(n1)
        if p0 is None or p1 is None or p0 == p1:
            return "纯注解变化"
    return None


def main():
    rows = [json.loads(l) for l in open(SRC)]
    removed_set = {(r["lib"], r["old_ver"], r["new_ver"], r["api"])
                   for r in rows if r["kind"] == "removed"}
    kept, drops = [], collections.Counter()
    for r in rows:
        why = classify(r, removed_set)
        if why:
            drops[why] += 1
        else:
            kept.append(r)
    byg = collections.defaultdict(list)
    for r in kept:
        byg[(r["lib"], r["old_ver"], r["new_ver"], r["kind"])].append(r)
    final = []
    for key, rs in byg.items():
        cap = 25 if key[3] == "removed" else 15
        rs.sort(key=lambda x: (len(x["api"].split(".")), len(x["api"])))
        final.extend(rs[:cap])
    with open(OUT, "w") as f:
        for r in final:
            f.write(json.dumps(r) + "\n")
    print(f"v2: 原始 {len(rows)} -> 规则后 {len(kept)} -> 限额后 {len(final)}")
    print("剔除原因:", dict(drops.most_common()))

    # ---- 回归: 对人工判例的召回 ----
    # 需要人工核验表 (未随仓库发布). 设 MIG_REVIEW_XLSX 指向它才跑这段.
    # Optional: recall check against the hand-annotated sheet, which is not
    # redistributed here. Set MIG_REVIEW_XLSX to run it.
    review = os.environ.get("MIG_REVIEW_XLSX")
    if not review or not os.path.exists(review):
        print("跳过人工判例回归 (未设 MIG_REVIEW_XLSX)")
        return
    import openpyxl
    wb = openpyxl.load_workbook(review)
    ws = wb["微迁移题池"]
    head = [c.value for c in ws[1]]
    idx = {k: head.index(k) for k in ("task_id", "库", "旧版", "新版", "API",
                                       "核验样本", "人工判定")}
    inv = {(r["lib"], r["old_ver"], r["new_ver"], r["api"]): r for r in rows}
    agree = collections.Counter()
    missed = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not (row[idx["核验样本"]] and str(row[idx["核验样本"]]).strip()):
            continue
        verdict = str(row[idx["人工判定"]] or "").strip()
        key = (row[idx["库"]], str(row[idx["旧版"]]), str(row[idx["新版"]]),
               row[idx["API"]])
        r = inv.get(key)
        why = classify(r, removed_set) if r else "不在v2清单"
        if verdict == "剔除":
            if why:
                agree["剔除-规则命中"] += 1
            else:
                agree["剔除-规则漏掉"] += 1
                missed.append((row[idx["task_id"]], key[3]))
        elif verdict == "合格":
            if why:
                agree["合格-被误杀"] += 1
                missed.append((row[idx["task_id"]], key[3], "误杀:" + why))
            else:
                agree["合格-规则放行"] += 1
    print("对 60 判例的回归:", dict(agree))
    for m in missed[:15]:
        print("  ", m)


if __name__ == "__main__":
    main()
