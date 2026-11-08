"""acmesdk 世界的月度维护闭环, 七条件比拼.

每月: 结账 (上月考试结果按 1/n 摊责注入过滤器) -> 维护 (预算 B: 探查 1 =
在当前版本跑笔记片段; 修复 3 = 读该子系统的 release notes 重写笔记片段,
机器验证) -> 考试 (注入 own + 同子系统笔记, 冻结断言, 必须调用当前 API).

条件: none / static / random / ttl / bayesian_agent / ours / oracle
ours 的因子 = 子系统 (所有方法都能看见的归属).
"""
import argparse
import collections
import concurrent.futures as cf
import json
import os
import random
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.append(os.path.dirname(HERE))
sys.path.append(os.path.join(os.path.dirname(HERE), "maskedmig"))   # llm / migration_loop
from sworld import SynthEnv, seed_dir                               # noqa: E402
from make_tasks import _fake_pkg, sim_run                          # noqa: E402
from llm import agent_loop                                         # noqa: E402
from migration_loop import assert_lines                            # noqa: E402
from bayesian_harness.filters import FilterConfig, build_filter           # noqa: E402
from lowq import lowq_ids, degrade_text                             # noqa: E402  低质量笔记设定 (--lowq-frac)
import cli_surface as CLI                                           # noqa: E402  C1: 命令行表面 (--surface cli)
SURFACE = "py"                                                      # main() 里按 --surface 设置


def is_cli():
    return SURFACE == "cli"


def make_env(seed, m, **kw):
    return CLI.CliEnv(seed, m, **kw) if is_cli() else SynthEnv(seed, m, **kw)


def run_code(env, code, timeout=45):
    return env.run_sh(code, timeout=timeout) if is_cli() else env.run_py(code, timeout=timeout)


def note_head(sub):
    return f"acmectl {sub}" if is_cli() else f"acmesdk.{sub}"


def tool_used(code):
    return (CLI.TOOL_NAME in code) if is_cli() else ("acmesdk" in code)

EXAM_SYS = """You maintain legacy code that uses acmesdk, an internal SDK of
your company. A small snippet that worked on an older acmesdk release now
FAILS on the current release. Repair it so the ORIGINAL assertions pass here.

acmesdk is internal: it is not on the public internet and you have no prior
knowledge of it. Its modules expose no listing (dir() is empty), no
docstrings, and error messages are generic. Your notes are the only
description you have. Trust them, and verify with a command when unsure.

Hard rules:
- You must NOT modify, remove, or reorder any `assert` line: they are the
  spec. Change only the other lines (imports, setup, the call, any
  post-processing needed so the asserts still hold). If a note tells you the
  function's output changed (new shape, new unit, new order), keep the
  asserts and compensate in code: unwrap, rescale, reorder, or convert the
  result back to what the asserts expect.
- The repaired snippet must still call acmesdk to do the work (the same
  function or its current replacement). Recomputing the values without
  acmesdk counts as failure.
- Work it out with bash (`python -c "..."` or write and run a file), exactly
  ONE bash code block with ONE command per response; each command costs one
  unit of a small budget.

When your repaired snippet passes here (or budget is gone), finish with:

FIXED {"code": "<the full repaired snippet, original assert lines intact>"}"""

EXAM_USER = """Environment: acmesdk {ver} (this is the CURRENT version).
The snippet below was written against acmesdk 1.1.0.
{notes}
## Legacy snippet (fails now)

```python
{snippet}
```

## Budget

{budget} commands.

Repair it, test it with bash, then hand in the FIXED line."""

REPAIR_SYS = """You maintain a notebook about acmesdk, your company's internal
SDK, for your future self. One note's example snippet no longer runs on the
current release. You are given the release notes for that subsystem since the
note was last valid.

Repair the example the way you would repair legacy code. Keep every `assert`
line of the stale example EXACTLY as it is, and change the other lines so
that the asserts pass on the current release: call the function by its
current name, pass parameters the way it now expects, and if its output
changed (new shape, new unit, new order) add the post-processing that turns
the new output back into the old value. The repaired example is then a
worked recipe for every legacy caller of this function, which is what your
future self needs most. In the note text state what changed and the
old-to-new mapping in one or two sentences (e.g. "result is now
{'value': x}, use result['value']"; "amounts are in minor units, divide by
100"; "call fmt_facet instead of format_facet, same arguments").

Test with bash first: exactly ONE bash code block with ONE command per
response, each command costs one unit of a small budget.

When the repaired example passes here with the original asserts, finish with:

REPAIRED {"snippet": "<repaired example, original assert lines intact>", "text": "<what changed + old-to-new mapping>"}"""

REPAIR_USER = """Environment: acmesdk {ver} (CURRENT).

## Your stale note (its assert lines are the contract; keep them verbatim)

{note}

## Release notes for acmesdk.{sub} since your note was last valid

{changelog}
{demo}
## Budget

{budget} commands.

Investigate, then hand in the REPAIRED line."""


def assert_stmts(code):
    """按语法树提取顶层 assert 语句并规范化 (对换行/分号/引号风格不敏感)."""
    import ast
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    return [ast.unparse(n) for n in tree.body if isinstance(n, ast.Assert)]


def asserts_preserved(orig_code, sub_code):
    """原断言语句必须按顺序原样出现在提交里 (语义级比对, 不看排版). cli 表面看 test 行."""
    if is_cli():
        return CLI.asserts_preserved_sh(orig_code, sub_code)
    o, s = assert_stmts(orig_code), assert_stmts(sub_code)
    if o is None or s is None:
        return False
    idx = 0
    for a1 in o:
        while idx < len(s) and s[idx] != a1:
            idx += 1
        if idx == len(s):
            return False
        idx += 1
    return True


# ---------------- 世界数据 ----------------
class World:
    def __init__(self, seed, months):
        self.seed, self.months = seed, months
        sd = seed_dir(seed)
        self.snaps = {m: json.load(open(f"{sd}/truth/spec_M{m:02d}.json"))
                      for m in range(1, months + 1)}
        self.fakes = {m: _fake_pkg(self.snaps[m]) for m in range(1, months + 1)}
        _f = CLI.FILES if is_cli() else {"tasks": "tasks.jsonl", "exams": "exams.jsonl", "notes": "notes_M01.jsonl"}
        self.tasks = {t["task_id"]: t for t in map(json.loads, open(f"{sd}/{_f['tasks']}"))}
        self.exams = collections.defaultdict(list)
        for e in map(json.loads, open(f"{sd}/{_f['exams']}")):
            if e["m"] <= months:
                self.exams[e["m"]].append(e)
        self.notes0 = [json.loads(l) for l in open(f"{sd}/{_f['notes']}")]
        self.sd = sd

    def current_name(self, api_id, m):
        """api 在月 m 的现名 (被删则沿接班链)."""
        st = self.snaps[m]
        a = st.get(api_id)
        while a is not None and not a["alive"] and a.get("succ"):
            a = st.get(a["succ"])
        return a["name"] if a and a["alive"] else None

    def name_history(self, api_id):
        names = set()
        for st in self.snaps.values():
            a = st.get(api_id)
            while a is not None:
                names.add(a["name"])
                a = st.get(a["succ"]) if a.get("succ") else None
        return names

    def changelog_excerpt(self, sub, m_from, m_to, names, cap=40):
        lines = []
        for m in range(max(2, m_from), m_to + 1):
            p = f"{self.sd}/changelog/M{m:02d}.md"
            if not os.path.exists(p):
                continue
            txt = open(p).read()
            sec = re.search(rf"## {re.escape(sub)}\n(.*?)(?:\n## |\Z)", txt, re.S)
            if not sec:
                continue
            hit = [l for l in sec.group(1).splitlines()
                   if l.startswith("- ") and any(f"`{sub}.{n}`" in l for n in names)]
            if hit:
                lines.append(f"### acmesdk 1.{m}.0")
                lines += hit
        if not lines:
            return "(no entries mention this API; check the subsystem's other changes)"
        return "\n".join(lines[:cap])

    def valid(self, snippet, m):
        if is_cli():
            return CLI.valid_sh(snippet, self.fakes[m], self.seed, m)
        return sim_run(snippet, self.fakes[m])


# ---------------- 维护算子 ----------------
_RENAME_RE = re.compile(r"`(\w+)\.(\w+)` was renamed to `(\w+)\.(\w+)`")
_REMOVED_RE = re.compile(r"`(\w+)\.(\w+)` was removed\. Use `(\w+)\.(\w+)` instead")
_PARAM_RE = re.compile(r"`(\w+)\.(\w+)`: parameter `(\w+)` was renamed to `(\w+)`")


def mech_repair(W, note, m):
    """机械修复 (2026-09-06): 只用变更日志里写明的改名 / 删除接班 / 参数改名, 逐条替换 import 与调用,
    断言行原样保留, 沙箱验证通过才算修好. 不调用模型, 成本按 --mech-cost 记. 失败返回 None (回退模型修复)."""
    names = W.name_history(note["note_id"])
    cl = W.changelog_excerpt(note["sub"], note.get("fresh_for", 1) + 1, m, names)
    snippet = note["snippet"]; changed = False
    for line in cl.splitlines():
        mo = _RENAME_RE.search(line) or _REMOVED_RE.search(line)
        if mo:
            _, old, _, new = mo.groups()
            if re.search(rf"\b{re.escape(old)}\b", snippet):
                snippet = re.sub(rf"\b{re.escape(old)}\b", new, snippet); changed = True
            continue
        mo = _PARAM_RE.search(line)
        if mo:
            _, _, oldp, newp = mo.groups()
            if re.search(rf"\b{re.escape(oldp)}\s*=", snippet):
                snippet = re.sub(rf"\b{re.escape(oldp)}(\s*=)", rf"{newp}\1", snippet); changed = True
    if not changed or not asserts_preserved(note["snippet"], snippet) or not W.valid(snippet, m):
        return None
    new = dict(note)
    new["snippet"] = snippet
    new["text"] = (f"acmesdk.{note['sub']} note, re-verified on acmesdk 1.{m}.0 (interface change applied from the release notes).\n"
                   f"Working example (verified):\n{snippet[:700]}")
    new["fresh_for"] = m
    return new


_CALL_LINE_RE = re.compile(r"^(\s*\w+\s*=\s*)(.+?)\s*$")


def _call_line(snippet):
    """找到片段里的调用行 (第一条 'x = <expr>(...)' 且非 import/assert/print), 返回 (行号, 前缀, 表达式)."""
    for i, line in enumerate(snippet.splitlines()):
        st = line.strip()
        if not st or st.startswith(("from ", "import ", "assert", "print")):
            continue
        mo = _CALL_LINE_RE.match(line)
        if mo and "(" in mo.group(2):
            return i, mo.group(1), mo.group(2)
    return None


def derive_template(old_snip, new_snip):
    """从一次成功修复的新旧片段抽改写规则. 模型的修法通常是两种: (a) 调用行之后、断言之前新增后处理语句
    (补字段 / 过滤 None / 列表推导 / 除以 100 ...); (b) 调用表达式外面包一层. 规则 = {post: 新增语句块, wrap: 包裹模式, var: 结果变量名}."""
    o, n = _call_line(old_snip), _call_line(new_snip)
    if not o or not n:
        return None
    oi, oprefix, oexpr = o
    ni, _, nexpr = n
    def post_of(lines, i):
        out = []
        for line in lines[i + 1:]:
            if line.strip().startswith(("assert", "print")):
                break
            out.append(line)
        return out
    post_old = post_of(old_snip.splitlines(), oi)
    post_new = post_of(new_snip.splitlines(), ni)
    wrap = nexpr.replace(oexpr, "\u27e8CALL\u27e9", 1) if (oexpr in nexpr and oexpr != nexpr) else None
    post = post_new if post_new != post_old else []
    if not post and not wrap:
        return None
    return {"post": post, "wrap": wrap, "var": oprefix.strip().rstrip("=").strip()}


def apply_template(W, note, tpl, m):
    """把规则套到兄弟笔记: 调用行按 wrap 改写, 其后插入 post 语句块 (结果变量名对齐), 断言保留, 沙箱验证."""
    c = _call_line(note["snippet"])
    if not c:
        return None
    i, prefix, expr = c
    var = prefix.strip().rstrip("=").strip()
    lines = note["snippet"].splitlines()
    new_call = prefix + (tpl["wrap"].replace("\u27e8CALL\u27e9", expr) if tpl.get("wrap") else expr)
    post = [re.sub(rf"\b{re.escape(tpl['var'])}\b", var, l) for l in tpl.get("post", [])] if tpl.get("var") and var != tpl.get("var") else list(tpl.get("post", []))
    # 去掉兄弟原有的后处理 (通常没有), 换成模板的
    j = i + 1
    while j < len(lines) and not lines[j].strip().startswith(("assert", "print")):
        j += 1
    lines = lines[:i] + [new_call] + post + lines[j:]
    snippet = "\n".join(lines) + ("\n" if note["snippet"].endswith("\n") else "")
    if not asserts_preserved(note["snippet"], snippet) or not W.valid(snippet, m):
        return None
    new = dict(note)
    new["snippet"] = snippet
    new["text"] = (f"acmesdk.{note['sub']} note, re-verified on acmesdk 1.{m}.0 (fix propagated from a sibling that shares the same implementation).\n"
                   f"Working example (verified):\n{snippet[:700]}")
    new["fresh_for"] = m
    return new


def repair_note(W, note, m, a, demos=None):
    """demos: 同组兄弟最近的修法示例 [(old_snippet, new_snippet), ...] (示例传播: 共因坏了一批, 修好一张给其余当范例)."""
    calls = a.repair_calls if not demos else max(1, a.demo_calls)
    env = make_env(W.seed, m, call_budget=calls)
    try:
        names = W.name_history(note["note_id"])
        cl = W.changelog_excerpt(note["sub"], note.get("fresh_for", 1) + 1, m, names)
        if is_cli():
            cl = CLI.changelog_cli(cl)
        demo_txt = ""
        if demos:
            parts = []
            for i, (o, n_) in enumerate(demos[:2], 1):
                parts.append(f"### Sibling {i}, before\n{o.strip()[:600]}\n### Sibling {i}, after (verified on {m and f'1.{m}.0'})\n{n_.strip()[:700]}")
            demo_txt = ("\n## How sibling functions that share the same implementation were just fixed\n\n"
                        "They broke for the same reason as your note; apply the analogous change.\n\n" + "\n\n".join(parts) + "\n")
        got, _, _ = agent_loop(
            env, a.port, a.model, CLI.REPAIR_SYS if is_cli() else REPAIR_SYS,
            (CLI.REPAIR_USER if is_cli() else REPAIR_USER).format(ver=f"1.{m}.0", note=note["text"][:900], sub=note["sub"],
                               changelog=cl[:2500], demo=demo_txt, budget=calls),
            "REPAIRED", calls + 4, max_tokens=1000)
        if not got:
            return None
        snip = str(got.get("snippet") or "")
        if not tool_used(snip) or not (CLI.test_lines(snip) if is_cli() else "assert" in snip):
            return None
        # 笔记自己的断言行必须原样保留 (修复 = 用新接口还原旧行为的范本)
        if not asserts_preserved(note["snippet"], snip):
            return None
        ok, _ = run_code(env, snip, timeout=45)
        if not ok:
            return None
        new = dict(note)
        new["snippet"] = snip[:900]
        new["text"] = (f"{note_head(note['sub'])} note, re-verified on {'acmectl' if is_cli() else 'acmesdk'} 1.{m}.0.\n"
                       + str(got.get("text") or "")[:400]
                       + f"\nWorking example (verified):\n{snip[:700]}")
        new["fresh_for"] = m
        return new
    finally:
        env.cleanup()


def cheap_ids(seed, ids, frac):
    """便宜修复的函数集合 (Table 4): 按种子固定抽样, 同一种子下所有策略、所有模型看到同一半."""
    if frac <= 0:
        return set()
    rng = random.Random(4242 + int(seed))
    ids = sorted(ids)
    return set(rng.sample(ids, int(round(frac * len(ids)))))


def cheapen(old, new, mode):
    """便宜修复: 片段已修好并验证 (new['snippet']), 但说明不更新.
    keep: 旧笔记的说明文字原样保留 (去掉里面的旧片段), 后面接新片段;  none: 只有新片段;  name: 只有函数名 (lowq 的写法)."""
    snip = new["snippet"]
    if mode == "name":
        text = degrade_text(new)
    elif mode == "none":
        text = f"Working example (verified):\n{snip[:700]}"
    else:
        prose = old["text"]
        cut = prose.find("Working example (verified):")
        if cut >= 0:
            prose = prose[:cut]
        else:
            old_lines = {l.strip() for l in old["snippet"].splitlines() if l.strip()}
            prose = "\n".join(l for l in prose.splitlines() if l.strip() not in old_lines)
        text = prose.rstrip() + f"\nWorking example (verified):\n{snip[:700]}"
    out = dict(new)
    out["text"] = text
    return out


REWRITE_SYS = """You maintain a notebook about acmesdk, your company's internal
SDK, for your future self. One note's example still runs on the current
release, but the note was patched in a hurry and its explanation is out of
date or missing. Rewrite the note fully.

Keep every `assert` line of the example EXACTLY as it is. You may improve the
other lines of the example if that makes it a clearer recipe, but it must
still pass on the current release. In the note text explain, in two or three
sentences, what the function does, how it is called on the current release
(name, parameters, output shape and unit), and what a legacy caller written
against acmesdk 1.1.0 has to change.

Test with bash first: exactly ONE bash code block with ONE command per
response, each command costs one unit of a small budget.

When the example passes here with the original asserts, finish with:

REPAIRED {"snippet": "<the example, original assert lines intact>", "text": "<the rewritten explanation>"}"""

REWRITE_USER = """Environment: acmesdk {ver} (CURRENT).

## Your note (its assert lines are the contract; keep them verbatim)

{note}

## Release notes for acmesdk.{sub} since the note was last rewritten

{changelog}

## Budget

{budget} commands.

Verify, then hand in the REPAIRED line."""


def rewrite_note(W, note, m, a):
    """完整重写一张 (策略认为) 有效的笔记: 片段保持可运行, 说明写全. 成本与修复相同 (repair_cost)."""
    calls = a.repair_calls
    env = make_env(W.seed, m, call_budget=calls)
    try:
        names = W.name_history(note["note_id"])
        cl = W.changelog_excerpt(note["sub"], note.get("fresh_for", 1) + 1, m, names)
        if is_cli():
            cl = CLI.changelog_cli(cl)
        got, _, _ = agent_loop(
            env, a.port, a.model, CLI.REWRITE_SYS if is_cli() else REWRITE_SYS,
            (CLI.REWRITE_USER if is_cli() else REWRITE_USER).format(ver=f"1.{m}.0", note=note["text"][:900], sub=note["sub"],
                                changelog=cl[:2500], budget=calls),
            "REPAIRED", calls + 4, max_tokens=1000)
        if not got:
            return None
        snip = str(got.get("snippet") or "")
        if not tool_used(snip) or not (CLI.test_lines(snip) if is_cli() else "assert" in snip):
            return None
        if not asserts_preserved(note["snippet"], snip):
            return None
        ok, _ = run_code(env, snip, timeout=45)
        if not ok:
            return None
        new = dict(note)
        new["snippet"] = snip[:900]
        new["text"] = (f"{note_head(note['sub'])} note, rewritten and re-verified on {'acmectl' if is_cli() else 'acmesdk'} 1.{m}.0.\n"
                       + str(got.get("text") or "")[:400]
                       + f"\nWorking example (verified):\n{snip[:700]}")
        new["fresh_for"] = m
        return new
    finally:
        env.cleanup()


def exam_one(W, e, sel, m, a):
    t = W.tasks[e["task_id"]]
    if a.proxy_exams:
        # 代理考试 (快速验证): 不调模型, 自家笔记当时有效 -> 做对; 否则按 none 条件的经验概率 (改名层 0, 其余约 0.45)
        own = next((n for n in sel if n["note_id"] == t["api_id"]), None)
        cur = W.current_name(t["api_id"], m)
        valid_own = bool(own) and W.valid(own["snippet"], m)
        if valid_own:
            solved = True
        else:
            renamed = (cur != t.get("api")) if t.get("api") else False
            solved = (not renamed) and (random.Random(hash((t["task_id"], m)) & 0xffff).random() < 0.45)
        return {"task_id": t["task_id"], "api_id": t["api_id"], "sub": t["sub"], "m": m, "encounter": e["encounter"],
                "solved": bool(solved), "err": "" if solved else "proxy", "submission": "", "injected": [n["note_id"] for n in sel]}
    env = make_env(W.seed, m, call_budget=a.task_budget)
    try:
        notes_txt = ""
        if sel:
            lowq = getattr(a, "_lowq_set", set())
            parts = [f"### note {i} ({note_head(n['sub'])})\n{n['title']}\n"
                     f"{degrade_text(n) if n['note_id'] in lowq else n['text'][:1200]}"
                     for i, n in enumerate(sel, 1)]
            notes_txt = (f"\n## Your notes on {'acmectl' if is_cli() else 'acmesdk'}\n\nWritten earlier; the {'tool' if is_cli() else 'SDK'} may have "
                         "moved since.\n\n" + "\n\n".join(parts) + "\n")
        got, _, _ = agent_loop(
            env, a.port, a.model, CLI.EXAM_SYS if is_cli() else EXAM_SYS,
            (CLI.EXAM_USER if is_cli() else EXAM_USER).format(ver=f"1.{m}.0", snippet=t["snippet"], notes=notes_txt,
                             budget=a.task_budget),
            "FIXED", a.task_budget + 5, max_tokens=1400)
        sub = (got or {}).get("code") or ""
        ok, err = False, "empty"
        if isinstance(sub, str) and sub.strip():
            ok2 = asserts_preserved(t["snippet"], sub)
            cur = W.current_name(t["api_id"], m)
            if not ok2:
                err = "assert modified"
            elif not tool_used(sub):
                err = f"bypass: {CLI.TOOL_NAME if is_cli() else 'acmesdk'} not used"
            elif cur and not re.search(rf"\b{re.escape(cur)}\b", sub):
                err = f"current API not called"
            else:
                ok, err = run_code(env, sub, timeout=60)
        return {"task_id": t["task_id"], "api_id": t["api_id"], "sub": t["sub"],
                "m": m, "encounter": e["encounter"], "solved": bool(ok),
                "err": (err or "")[:150], "submission": str(sub)[:600],
                "injected": [n["note_id"] for n in sel]}
    finally:
        env.cleanup()


_LLM_GRAPH = {}


def factor_parents(W, k, sub, a, cond):
    """一张笔记挂哪些共享变量 (--factors): sub = 声明的部门 (现状); kernel = 真值内核模板 (上界);
    both = 部门 + 内核; llm:<json> = LLM 建的图 (note_id -> [[factor, weight], ...]); none = 不挂."""
    mode = a.factors
    pas, w = [], {}
    if mode in ("sub", "both") or mode.startswith("llm"):
        pas.append("sub:" + sub); w["sub:" + sub] = 1.0
    if mode in ("kernel", "both"):
        kern = W.snaps[1].get(k, {}).get("kernel")
        if kern:
            pas.append("kernel:" + kern); w["kernel:" + kern] = 1.0
    if mode.startswith("llm"):
        path = mode.split(":", 1)[1]
        if path not in _LLM_GRAPH:
            _LLM_GRAPH[path] = json.load(open(path))
        for f, wt in _LLM_GRAPH[path].get(k, []):
            if wt > 0:
                pas.append(f); w[f] = float(wt)
    if "tags" in cond:
        for tg in W.snaps[1].get(k, {}).get("tags", []):
            pas.append("tag:" + tg); w["tag:" + tg] = 1.0
    if mode == "none":
        pas, w = [], {}
    return pas, w


def run_condition(W, cond, a):
    notes = {}
    for n in W.notes0:
        nn = dict(n)
        nn["fresh_for"] = 1
        notes[n["note_id"]] = nn
    ids = sorted(notes)
    flt = None
    if cond not in ("none", "static", "oracle"):
        _cfg = FilterConfig()
        for kv in (a.cfg or []):                          # --cfg hmm_bg=0.05 --cfg hmm_rho=0.15 : 覆盖滤波器常数
            k_, v_ = kv.split("=", 1); setattr(_cfg, k_, type(getattr(_cfg, k_))(v_))
        flt = build_filter(cond, _cfg)
        rng_c = random.Random(1000 + W.seed)
        subs_all = sorted({notes[k]["sub"] for k in ids})
        n_corrupt = 0
        for k in ids:
            sub = notes[k]["sub"]
            if a.factor_corrupt > 0 and rng_c.random() < a.factor_corrupt:
                sub = rng_c.choice([s for s in subs_all if s != notes[k]["sub"]])   # 声明错子系统
                n_corrupt += 1
            pas, wts = factor_parents(W, k, sub, a, cond)
            for f in pas:
                flt.add_factor(f, t=0)
            try:
                flt.add_entry(k, parents=tuple(pas), t=0, weights=wts)
            except TypeError:                                    # 旧滤波器不带权重
                flt.add_entry(k, parents=tuple(pas), t=0)
        if a.factor_corrupt > 0:
            print(f"  [{cond}] 因子声明损坏 {n_corrupt}/{len(ids)} 张笔记", flush=True)
    known_broken, ledger = set(), []
    retired = set()                      # 丢弃动作: 质量太低的过期笔记不再修, 也不再注入考试
    cheap_set = getattr(a, "_cheap_set", set())
    last_repair, repaired_month = {}, {}        # 笔记 -> 最近一次修复是 cheap/full; 笔记 -> 最近修好的月份
    broke_at, caught_set = {}, set()            # 断裂事件: 笔记 -> 最近一次失效的月份; 一个周期内被探出的 (笔记, 月)
    valid_after = {}                            # 上月维护结束后笔记是否有效 (用来判定新的断裂)

    def _mark(k, m_, kind=None):
        last_repair[k] = kind or ("cheap" if k in cheap_set else "full")
        repaired_month[k] = m_
    demo_store = {}                      # group -> [(m, (old, new))] 同组修法示例 (示例传播)
    carry_tpls = {}                      # group -> [(m_derived, tpl)] 跨月保留的改写规则 (2 个月内有效)
    use_count, stats = collections.Counter(), collections.Counter()
    out = open(f"{a.out_prefix}_{cond}.jsonl", "w")

    for m in range(2, W.months + 1):
        t0 = time.time()
        pre = {k: W.valid(notes[k]["snippet"], m) for k in ids} if cond != "none" else {}
        month = collections.Counter()
        grp_month = None
        if cond != "none":
            for k in ids:
                if not pre[k] and valid_after.get(k, True) and k not in retired:
                    broke_at[k] = m; month["breaks"] += 1          # 新断裂: 上月维护后还有效, 这个月坏了
        if cond not in ("none", "static"):
            if flt is not None:
                for solved, read in ledger:
                    if read:
                        w = 1.0 / len(read)
                        for nid in read:
                            if nid in notes:
                                flt.update(nid, 1 if solved else 0, t=m,
                                           msg=None if solved else "env_changed", weight=w)
            if flt is not None and cheap_set:
                # Table 4 逐月记录 (探测之前): 每张笔记的排序量 (ours: 过期概率; BA: 后验分; 两计数器: 月龄) 与质量估计,
                # 按 (poor = 最近一次修复是便宜修复, valid = 接口当下有效) 分四组累加; 另按修复种类累加有效笔记的质量估计.
                _psc = getattr(flt, "probe_score", None) or flt.stale_score
                _rq = getattr(flt, "rank_quantity", None)
                _g = {key: [0, 0.0, 0.0] for key in ("pv", "gs", "gv", "ps")}
                _qv = {"cheap": [0, 0.0], "full": [0, 0.0]}
                for k in ids:
                    if k in retired:
                        continue
                    sc = _rq(k, m) if _rq else _psc(k, m)
                    ql = flt.reliability(k)
                    key = ("p" if last_repair.get(k) == "cheap" else "g") + ("v" if pre[k] else "s")
                    _g[key][0] += 1; _g[key][1] += sc; _g[key][2] += ql
                    if pre[k] and last_repair.get(k) in _qv:
                        _qv[last_repair[k]][0] += 1; _qv[last_repair[k]][1] += ql
                grp_month = {"score": _g, "qual_valid": _qv}
            left = float(a.maint_budget)
            groups = {}                      # note -> set of group ids (传播 / 示例用: 部门 / LLM 家族 / 全部)
            if a.propagate != "none" or a.demo:      # 09-06 19:05 bug: 只开 --demo 时组为空, 范例从未生效
                for k in ids:
                    if a.propagate == "all":
                        groups[k] = {"all"}
                    else:
                        pas, wts = factor_parents(W, k, notes[k]["sub"], a, cond)
                        groups[k] = {f for f in pas if wts.get(f, 1.0) >= a.prop_min_w}

            repaired_now = set()
            llm_fixed = []                   # (k, old_snip, new_snip) 供传播
            for g in list(carry_tpls):       # 过期规则清理
                carry_tpls[g] = [(mm, t) for mm, t in carry_tpls[g] if m - mm <= 2]
                if not carry_tpls[g]:
                    del carry_tpls[g]
            month_tpls = {g: [t for _, t in v] for g, v in carry_tpls.items()}   # 本月可用规则 (含上月的)

            def try_template(k):
                """已知坏了的笔记: 先用同组规则修 (每次验证 1 单位), 成功返回 True. 最便宜的修复手段, 排在机械修复之后、模型之前."""
                nonlocal left
                if a.propagate == "none":
                    return False
                cands_t = [t for g in groups.get(k, set()) for t in month_tpls.get(g, [])]
                for t in cands_t[:a.prop_tries]:
                    if left < 1:
                        return False
                    left -= 1; month["prop_tries"] += 1
                    new_k = apply_template(W, notes[k], t, m)
                    if new_k is not None:
                        notes[k] = new_k; repaired_now.add(k); _mark(k, m)
                        month["repairs"] += 1; month["repair_ok"] += 1; month["prop_ok"] += 1
                        if flt is not None:
                            flt.on_repair(k, m)
                        return True
                return False

            def demos_for(k):
                if not a.demo:
                    return None
                ex = [d for g in groups.get(k, set()) for (mm, d) in demo_store.get(g, []) if m - mm <= 2]
                return ex[-2:] if ex else None

            def do_repair(k):
                month["repairs"] += 1
                old_snip = notes[k]["snippet"]
                dm = demos_for(k)
                if dm:
                    month["demo_repairs"] += 1
                new = repair_note(W, notes[k], m, a, demos=dm)
                if new is not None:
                    if k in cheap_set:
                        new = cheapen(notes[k], new, a.cheap_mode)      # 便宜修复: 片段修好, 说明不更新
                    _mark(k, m)
                    notes[k] = new
                    month["repair_ok"] += 1
                    if dm:
                        month["demo_ok"] += 1
                    repaired_now.add(k)
                    llm_fixed.append((k, old_snip, new["snippet"]))
                    for g in groups.get(k, set()):
                        demo_store.setdefault(g, []).append((m, (old_snip, new["snippet"])))
                if flt is not None:
                    if new is None and a.keep_stale_on_failed_repair:
                        # Reviewer zpd8 Q1 variant: a failed repair leaves the note broken, so record it
                        # as an invalid observation and put it back in the known-broken queue; it is
                        # retried next period (like the Oracle) without spending another probe.
                        flt.observe_probe(k, False, m)
                        known_broken.add(k)
                    else:
                        flt.on_repair(k, m)         # paper default: reset after any repair attempt


            def propagate(k, old_snip, new_snip):
                """修好 k 后, 沿图把改写规则套到同组兄弟: 先探 (1 单位) 确认坏了, 再套规则验证 (1 单位)."""
                nonlocal left
                if a.propagate == "none" or not groups.get(k):
                    return
                tpl = derive_template(old_snip, new_snip)
                if tpl is None:
                    return
                for g in groups[k]:
                    month_tpls.setdefault(g, []).append(tpl)
                    carry_tpls.setdefault(g, []).append((m, tpl))
                sibs = [j for j in ids if j != k and j not in repaired_now and (groups.get(j, set()) & groups[k])
                        and not (flt is not None and getattr(flt, "entries", {}).get(j, {}).get("obs_t") == m and flt.stale_score(j, m) < 0.5)]
                if flt is not None:
                    sibs.sort(key=lambda j: -flt.stale_score(j, m) * (1.0 + use_count[j]))
                else:
                    sibs.sort(key=lambda j: -use_count[j])
                for j in sibs[:a.prop_max]:
                    if left < 2:
                        break
                    left -= 1; month["probes"] += 1; month["prop_probes"] += 1
                    ok_j = W.valid(notes[j]["snippet"], m)
                    if flt is not None:
                        flt.observe_probe(j, ok_j, m)
                    if ok_j:
                        continue
                    cands_t = [tpl] + [t for g in (groups.get(j, set()) & groups[k]) for t in month_tpls.get(g, []) if t is not tpl]
                    new_j = None
                    for t in cands_t[:a.prop_tries]:
                        if left < 1:
                            break
                        left -= 1; month["prop_tries"] += 1
                        new_j = apply_template(W, notes[j], t, m)
                        if new_j is not None:
                            break
                    if new_j is not None:
                        notes[j] = new_j; repaired_now.add(j); _mark(j, m)
                        month["repairs"] += 1; month["repair_ok"] += 1; month["prop_ok"] += 1
                        known_broken.discard(j)
                        if flt is not None:
                            flt.on_repair(j, m)
                    else:
                        known_broken.add(j)

            def try_mech(k):
                """机械修复优先 (所有方法一致): 成功则记 mech_cost 并返回 True."""
                nonlocal left
                if not a.mech_repair or left < a.mech_cost:
                    return False
                new = mech_repair(W, notes[k], m)
                if new is None:
                    return False
                notes[k] = new; _mark(k, m)
                left -= a.mech_cost
                month["repairs"] += 1; month["repair_ok"] += 1; month["mech_ok"] += 1
                if flt is not None:
                    flt.on_repair(k, m)
                return True

            for k in sorted(known_broken, key=lambda k: -use_count[k]):
                if try_mech(k) or try_template(k):
                    known_broken.discard(k)
                    continue
                if left < a.repair_cost:
                    break
                left -= a.repair_cost
                known_broken.discard(k)
                do_repair(k)
            while llm_fixed:
                propagate(*llm_fixed.pop(0))
            rw_sel = []
            if a.rewrite_lowq and cond != "oracle" and flt is not None:
                # 低质量重写 (Table 4 最后一行), 预留部分: 按论文原句 "过期队列空了再用剩余预算" 在预算 12/24 下永远轮不到
                # (背景漂移让候选的过期概率都高于探测下限, 预算总被探测/修复花光), 所以每期先预留 rewrite_reserve 份额,
                # 给 (策略认为) 有效、修过、且估计质量低于其子系统合并均值的笔记, 按 (1 - 质量) x (1 + 使用量) 从高到低.
                # 没有这样的候选, 预留放回探测 (此时与基本策略完全一致).
                _n_rw = int((a.maint_budget * a.rewrite_reserve) // a.repair_cost)
                _pm = getattr(flt, "_pool_mean", None)
                _gm = sum(flt.reliability(i) for i in ids) / max(len(ids), 1)
                _cands_rw = [k for k in ids if k not in known_broken and k not in retired and repaired_month.get(k) != m
                             and notes[k].get("fresh_for", 1) > 1
                             and flt.reliability(k) < (_pm(k) if _pm else _gm)]
                _cands_rw.sort(key=lambda j: -(1.0 - flt.reliability(j)) * (1.0 + use_count[j]))
                for k in _cands_rw[:_n_rw]:
                    if left < a.repair_cost:
                        break
                    left -= a.repair_cost; rw_sel.append(k)
            if cond == "oracle":
                broken = sorted([k for k in ids if not pre[k]], key=lambda k: -use_count[k])
                sel_r = []
                for k in broken:
                    if try_mech(k):
                        continue
                    if left < a.repair_cost:
                        break
                    left -= a.repair_cost
                    sel_r.append(k)
                with cf.ThreadPoolExecutor(min(a.workers, max(len(sel_r), 1))) as ex:
                    list(ex.map(do_repair, sel_r))
            else:
                psc = getattr(flt, "probe_score", None) or flt.stale_score
                # 排序规则消融 (--rank-mode): stale_x_use = 过期概率 x (1+使用量) [论文默认]; stale_only = 只看过期概率; use_only = 只看使用量
                _q = getattr(getattr(flt, "cfg", None), "q", 0.1)
                def rank_key(j):
                    if a.rank_mode == "stale_only": return psc(j, m)
                    if a.rank_mode == "use_only": return (1.0 + use_count[j]) + 1e-6 * psc(j, m)
                    if a.rank_mode == "value":
                        # 决策论指标: 坏掉的概率 x 未来用到的次数 x 修好后能多做对多少 (质量 - 蒙对概率)
                        return psc(j, m) * (1.0 + use_count[j]) * max(0.0, flt.reliability(j) - _q)
                    return psc(j, m) * (1.0 + use_count[j])
                pending = []
                cands = [k for k in ids if k not in known_broken and k not in retired and k not in rw_sel]
                if not a.adaptive_probe:
                    cands.sort(key=lambda j: -rank_key(j))
                # 自适应探测 (2026-09-06): 每探一次重排剩余候选. 无结构的方法分数不变 (等价于固定顺序);
                # 有共享状态的方法在探到一张坏笔记后, 兄弟笔记立刻升嫌疑并被优先探, 连坐当月就起作用.
                while left >= 1 and cands:
                    if a.adaptive_probe:
                        k = max(cands, key=rank_key)
                    else:
                        k = cands[0]
                    if a.rank_mode in ("use_only", "value"):
                        # 排序不再单调于过期概率时, 停止条件改为"没有任何候选看起来过期"
                        if max(flt.stale_score(j, m) for j in cands) < a.maint_floor:
                            break
                    elif flt.stale_score(k, m) < a.maint_floor:
                        break
                    cands.remove(k)
                    left -= 1
                    month["probes"] += 1
                    verdict = pre[k]
                    flt.observe_probe(k, verdict, m)
                    if verdict:
                        month["probe_valid"] += 1                  # 白探: 探到的是有效笔记 (被误报浪费的预算)
                    if not verdict and broke_at.get(k) is not None and m - broke_at[k] <= 1 and (k, broke_at[k]) not in caught_set:
                        caught_set.add((k, broke_at[k])); month["caught"] += 1     # 断裂后一个周期内被探出 (Table 4 caught)
                    if not verdict:
                        # 丢弃: 这条笔记就算修好也帮不上忙 (估计质量低于门槛), 不花预算修, 从笔记本里删掉
                        if a.retire_threshold > 0 and flt.reliability(k) < a.retire_threshold:
                            retired.add(k); month["retired"] += 1
                            continue
                        if try_mech(k) or try_template(k):
                            pass
                        elif left >= a.repair_cost:
                            left -= a.repair_cost
                            pending.append(k)
                        else:
                            known_broken.add(k)
                # 示例修复更便宜: 有同组范例的修复按 demo_cost 计, 先按 repair_cost 扣的差额退回
                for k in pending:
                    if demos_for(k):
                        left += (a.repair_cost - a.demo_cost)
                with cf.ThreadPoolExecutor(min(a.workers, max(len(pending), 1))) as ex:
                    list(ex.map(do_repair, pending))
                while llm_fixed:
                    propagate(*llm_fixed.pop(0))
            if a.rewrite_lowq and cond != "oracle" and flt is not None:
                # 剩余预算 (论文原句): 过期队列处理完还有剩, 也按 (1 - 估计质量) x (1 + 使用量) 从高到低对修过的有效笔记做完整重写.
                _more = [k for k in ids if k not in known_broken and k not in retired and k not in repaired_now
                         and k not in rw_sel and notes[k].get("fresh_for", 1) > 1 and repaired_month.get(k) != m]
                _more.sort(key=lambda j: -(1.0 - flt.reliability(j)) * (1.0 + use_count[j]))
                for k in _more:
                    if left < a.repair_cost:
                        break
                    left -= a.repair_cost; rw_sel.append(k)
                _rw = rw_sel

                def do_rewrite(k):
                    month["rewrites"] += 1
                    new = rewrite_note(W, notes[k], m, a)
                    if new is not None:
                        notes[k] = new
                        _mark(k, m, "full")
                        month["rewrite_ok"] += 1
                        flt.on_repair(k, m)
                with cf.ThreadPoolExecutor(min(a.workers, max(len(_rw), 1))) as ex:
                    list(ex.map(do_rewrite, _rw))
            month["budget_spent"] = a.maint_budget - left
        if cond != "none":
            valid_after = {k: (bool(pre.get(k)) or repaired_month.get(k) == m) for k in ids}
        ledger = []
        fresh_n = sum(1 for k in ids if pre.get(k)) if cond != "none" else 0

        todo = W.exams.get(m, [])
        if a.n_exams and stats["n"] + len(todo) > a.n_exams:
            todo = todo[:max(0, a.n_exams - stats["n"])]

        def sel_for(e):
            if cond == "none":
                return []
            own = None if e["api_id"] in retired else notes.get(e["api_id"])
            sibs = sorted([notes[k] for k in ids if notes[k]["sub"] == e["sub"] and k != e["api_id"]
                           and k not in retired],
                          key=lambda n: -use_count[n["note_id"]])[:a.inject - 1]
            return ([own] if own else []) + sibs

        recs = []
        if todo:
            sels = [sel_for(e) for e in todo]
            with cf.ThreadPoolExecutor(min(a.workers, max(len(todo), 1))) as ex:
                recs = list(ex.map(lambda z: exam_one(W, z[0], z[1], m, a), zip(todo, sels)))
        for e, rec in zip(todo, recs):
            rec["own_note_valid"] = (bool(pre.get(e["api_id"])) if (cond != "none" and e["api_id"] in pre) else None)
            rec["own_repair"] = last_repair.get(e["api_id"])          # None / cheap / full: 自家笔记最近一次修复的种类
        for rec in recs:
            rec["condition"] = cond
            out.write(json.dumps(rec) + "\n")
            stats["n"] += 1
            stats["solved"] += rec["solved"]
            for nid in rec["injected"]:
                use_count[nid] += 1
            ledger.append((rec["solved"], rec["injected"]))
        out.flush()
        with open(f"{a.out_prefix}_{cond}.maint.jsonl", "a") as mf:      # 每月维护统计, 供表格的 probes/repairs 列
            rel_p10 = None
            if flt is not None and hasattr(flt, "reliability"):
                rr = sorted(flt.reliability(k) for k in ids)
                rel_p10 = round(rr[len(rr) // 10], 3)          # 质量估计的低十分位, 用来看门槛定得合不合适
            mf.write(json.dumps({"m": m, "valid_notes": fresh_n, "n_notes": len(ids),
                                 "n_retired": len(retired), "rel_p10": rel_p10, **{k: month[k] for k in ("probes", "probe_valid", "retired", "repairs", "repair_ok", "mech_ok", "prop_probes", "prop_tries", "prop_ok", "demo_repairs", "demo_ok", "budget_spent", "breaks", "caught", "rewrites", "rewrite_ok")},
                                 "grp": grp_month}) + "\n")
        for key in ("probes", "repairs", "repair_ok", "mech_ok", "prop_ok"):
            stats[key] += month[key]
        if todo or month["repairs"]:
            print(f"  [{cond}] M{m:02d}: {len(recs)} 题 {sum(r['solved'] for r in recs)} 解出, "
                  f"有效笔记 {fresh_n}/{len(ids)}, probes {month['probes']} repairs "
                  f"{month['repairs']}(ok {month['repair_ok']}), {time.time()-t0:.0f}s", flush=True)
    out.close()
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--months", type=int, default=36)
    ap.add_argument("--model", default="Qwen/Qwen3.5-27B")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--conditions", default="none,static,oracle")
    ap.add_argument("--maint-budget", type=float, default=24.0)
    ap.add_argument("--repair-cost", type=float, default=3.0)
    ap.add_argument("--repair-calls", type=int, default=4)
    ap.add_argument("--maint-floor", type=float, default=0.02)
    ap.add_argument("--task-budget", type=int, default=3)
    ap.add_argument("--inject", type=int, default=3)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--n-exams", type=int, default=0)
    ap.add_argument("--out-prefix", required=True)
    ap.add_argument("--factor-corrupt", type=float, default=0.0,
                    help="ours 的因子声明按此概率随机指错子系统 (稳健性消融)")
    ap.add_argument("--cfg", action="append", help="覆盖 FilterConfig 字段, 形如 hmm_bg=0.05 (可多次)")
    ap.add_argument("--factors", default="sub", help="共享变量来源: sub | kernel | both | llm:<graph.json> | none")
    ap.add_argument("--propagate", default="none", help="沿图传播修复: none | graph (按 --factors 的组) | all (不看图, 所有笔记)")
    ap.add_argument("--prop-max", type=int, default=10, help="每次修复最多向多少个兄弟传播")
    ap.add_argument("--prop-min-w", type=float, default=0.5, help="LLM 图边权低于此值的兄弟不传播")
    ap.add_argument("--prop-tries", type=int, default=2, help="每个兄弟最多试几条规则 (每次验证 1 单位)")
    ap.add_argument("--proxy-exams", type=int, default=0, help="1 = 考试不调模型 (快速验证维护策略: 自家笔记有效即做对)")
    ap.add_argument("--demo", type=int, default=0, help="1 = 示例传播: 修复时附上同组兄弟最近的修法 (按 --factors 的组)")
    ap.add_argument("--demo-cost", type=float, default=2.0, help="带范例的修复成本 (少探索)")
    ap.add_argument("--demo-calls", type=int, default=2, help="带范例的修复允许的命令数")
    ap.add_argument("--mech-repair", type=int, default=0,
                    help="1 = 接口类变动先按变更日志机械修复 (成本 --mech-cost), 失败回退模型修复")
    ap.add_argument("--mech-cost", type=float, default=1.0)
    ap.add_argument("--retire-threshold", type=float, default=0.0,
                    help="丢弃动作: 探出过期且估计质量低于此值的笔记直接丢弃 (不花预算修, 不再注入考试). 0 = 关闭")
    ap.add_argument("--rank-mode", default="stale_x_use", choices=["stale_x_use", "stale_only", "use_only", "value"],
                    help="探测排序规则: stale_x_use (论文默认) | stale_only 只按过期概率 | use_only 只按使用量 | value 决策论指标 过期x使用量x(质量-q)")
    ap.add_argument("--adaptive-probe", type=int, default=0,
                    help="1 = 每探一次重排剩余候选 (连坐当月生效); 0 = 旧行为, 月初排一次")
    ap.add_argument("--lowq-mode", default="random", help="random: 随机抽 API; subsystem: 整个子系统一起低质量")
    ap.add_argument("--lowq-frac", type=float, default=0.0,
                    help="低质量笔记设定: 固定抽这一比例的 API, 其笔记在考试时只显示名字不显示用例 (探测/修复不受影响)")
    ap.add_argument("--cheap-frac", type=float, default=0.0,
                    help="Table 4 (可分性): 固定抽这一比例的函数, 其笔记过期后只做便宜修复 (片段修好并验证, 说明不更新). 0 = 关闭")
    ap.add_argument("--cheap-mode", default="keep", choices=["keep", "none", "name"],
                    help="便宜修复的笔记文本: keep = 旧说明 + 新片段 (论文默认); none = 只有新片段; name = 只有函数名")
    ap.add_argument("--rewrite-reserve", type=float, default=1.0 / 3,
                    help="低质量重写每期预留的预算份额 (按整次重写取整: 预算 12 -> 1 次, 24 -> 2 次); 没有候选就放回探测")
    ap.add_argument("--rewrite-lowq", type=int, default=0,
                    help="1 = 过期队列空了以后, 剩余预算按估计质量从低到高 (乘使用量) 对有效笔记做完整重写 (Table 4 最后一行)")
    ap.add_argument("--surface", default="py", choices=["py", "cli"],
                    help="知识表面: py = acmesdk Python API 笔记 (默认); cli = acmectl 命令行用法笔记 (C1, 读 *_cli 文件)")
    ap.add_argument("--keep-stale-on-failed-repair", type=int, default=0,
                    help="reviewer zpd8 Q1: 1 = after a failed repair keep the note marked broken and retry it "
                         "next period (0 = paper default, reset after any attempt)")
    ap.add_argument("--world-suffix", default="",
                    help="变体世界目录后缀 (等价于 ACME_WORLD_SUFFIX 环境变量)")
    a = ap.parse_args()
    global SURFACE
    SURFACE = a.surface
    if a.world_suffix:
        os.environ["ACME_WORLD_SUFFIX"] = a.world_suffix
    import llm_backend
    print("模型后端:", llm_backend.install(a.model), a.model, flush=True)
    W = World(a.seed, a.months)
    conds = [c for c in a.conditions.split(",") if c]
    n_ex = sum(len(v) for v in W.exams.values())
    print(f"{'acmectl 命令行' if is_cli() else 'acmesdk'} 世界 seed{a.seed}: {len(W.notes0)} 张笔记, {n_ex} 次考试 / {a.months} 个月, "
          f"条件 {conds}, 维护 {a.maint_budget:g}/月", flush=True)
    os.makedirs(os.path.dirname(os.path.abspath(a.out_prefix)) or ".", exist_ok=True)
    a._lowq_set = lowq_ids(a.seed, [n["note_id"] for n in W.notes0], a.lowq_frac, mode=a.lowq_mode,
                           subs={n["note_id"]: n["sub"] for n in W.notes0})
    if a._lowq_set:
        print(f"低质量笔记设定: {len(a._lowq_set)}/{len(W.notes0)} 张笔记考试时只显示名字 (--lowq-frac {a.lowq_frac})", flush=True)
        with open(f"{a.out_prefix}.lowq.json", "w") as f:
            json.dump(sorted(a._lowq_set), f)
    a._cheap_set = cheap_ids(a.seed, [n["note_id"] for n in W.notes0], a.cheap_frac)
    if a._cheap_set:
        print(f"便宜修复设定: {len(a._cheap_set)}/{len(W.notes0)} 个函数的笔记只做便宜修复 (--cheap-mode {a.cheap_mode})", flush=True)
        with open(f"{a.out_prefix}.cheap.json", "w") as f:
            json.dump(sorted(a._cheap_set), f)
    results = {}
    for cond in conds:
        print(f"== {cond}", flush=True)
        results[cond] = run_condition(W, cond, a)
    print(f"\n{'条件':<15}{'solved':>8}{'probes':>8}{'repairs':>9}{'rep_ok':>7}")
    for cond in conds:
        s = results[cond]
        n = max(s["n"], 1)
        print(f"{cond:<15}{s['solved']/n*100:>7.1f}%{s['probes']:>8}{s['repairs']:>9}{s['rep_ok']:>7}")
    if llm_backend.USAGE["calls"]:
        u = llm_backend.USAGE
        print(f"API 用量: {u['calls']} 次调用, 输入 {u['in']/1e6:.2f}M, 输出 {u['out']/1e6:.2f}M, 思考 {u['think']/1e6:.2f}M tokens", flush=True)


if __name__ == "__main__":
    main()
