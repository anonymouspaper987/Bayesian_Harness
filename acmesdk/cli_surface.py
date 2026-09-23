"""命令行表面 (C1 第三个知识领域): 同一个 acmesdk 世界, 换成 shell 脚本 + acmectl 命令 + test 断言.
提供 loop.py --surface cli 需要的一切: 渲染 shell 片段, 进程内模拟, test 行校验, 提示词, 变更日志转写, 沙箱.
"""
import importlib.util
import json
import os
import re
import shlex
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from sworld import SynthEnv                                          # noqa: E402

CLI_DIR = os.path.join(HERE, "cli")
import importlib.machinery
_loader = importlib.machinery.SourceFileLoader("acmectl_launcher", os.path.join(CLI_DIR, "acmectl"))   # 文件没有 .py 后缀, 要显式指定加载器
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
_launcher = importlib.util.module_from_spec(_spec); _loader.exec_module(_launcher)
parse_args, render_out = _launcher.parse_args, _launcher.render      # 与真实 acmectl 完全同一份解析/输出代码


# ---------------- 渲染 ----------------
def sh_literal(v):
    """一个参数值 -> shell 词. 字符串若本身不是合法 JSON 就裸写 (可读), 否则写成 JSON."""
    if isinstance(v, str):
        try:
            json.loads(v); s = json.dumps(v)
        except Exception:
            s = v
    else:
        s = json.dumps(v, separators=(",", ":"))
    return shlex.quote(s)


def render_call_sh(spec, kw, variant):
    """三种写法: 0 全位置; 1 全旗标 (省略有默认值的); 2 首位置其余旗标. 与 make_tasks.render_call 一一对应."""
    core = [p for p in spec["params"] if not p.get("extra")]
    extras = [p for p in spec["params"] if p.get("extra")]
    if variant == 0:
        parts = [sh_literal(kw[p["n"]]) for p in core]
        parts += [f"--{p['n']} {sh_literal(kw[p['n']])}" for p in extras]
    elif variant == 1:
        parts = [f"--{p['n']} {sh_literal(kw[p['n']])}" for p in spec["params"] if "d" not in p]
    else:
        parts = [sh_literal(kw[core[0]["n"]])] if core else []
        parts += [f"--{p['n']} {sh_literal(kw[p['n']])}" for p in core[1:]]
        parts += [f"--{p['n']} {sh_literal(kw[p['n']])}" for p in extras]
    return f"acmectl {spec['sub']} {spec['name']} {' '.join(parts)}".rstrip()


def make_snippet_sh(spec, kw, variant, expected):
    # set -e: test 失败或命令失败就退出非零 (shell 里 test 不像 assert 会中止脚本); 判分器另外用 bash -e 强制
    return (f"set -e\n"
            f"out=$({render_call_sh(spec, kw, variant)})\n"
            f"test \"$out\" = {shlex.quote(render_out(expected))}\n"
            f"echo Success\n")


# ---------------- test 行 (冻结断言) ----------------
def test_lines(code):
    return [l.strip() for l in code.splitlines() if re.match(r"\s*(test\s|\[\s|\[\[\s)", l)]


def asserts_preserved_sh(orig, sub):
    """原脚本的 test 行必须按顺序原样出现在提交里."""
    o = test_lines(orig); s = test_lines(sub)
    idx = 0
    for a in o:
        while idx < len(s) and s[idx] != a:
            idx += 1
        if idx == len(s):
            return False
        idx += 1
    return bool(o)


# ---------------- 进程内模拟 (只处理规范形态: 一行 acmectl 赋值 + test 行) ----------------
_ASSIGN = re.compile(r"^\s*(\w+)=\$\((acmectl\s[^|]*)\)\s*$")


def sim_run_sh(snippet, mods):
    """返回 True/False; 形态不规范 (有管道 / 多条命令 / 非 acmectl) 返回 None, 调用方改用真实 bash."""
    lines = [l for l in snippet.splitlines() if l.strip() and not l.strip().startswith("#")]
    cmds = [(_ASSIGN.match(l), l) for l in lines]
    assigns = [m for m, _ in cmds if m]
    tests = test_lines(snippet)
    others = [l for m, l in cmds if not m and not re.match(r"\s*(test\s|\[\s|\[\[\s|echo\s|set\s+-)", l)]
    if len(assigns) != 1 or others or len(tests) < 1:
        return None
    var, cmd = assigns[0].group(1), assigns[0].group(2)
    try:
        argv = shlex.split(cmd)
        if len(argv) < 3 or argv[0] != "acmectl":
            return None
        sub, name = argv[1], argv[2]
        mod = mods.get(f"acmesdk.{sub}")
        if mod is None:
            return False
        fn = getattr(mod, name)                       # 未知命令 -> AttributeError -> 失败
        args, kwargs = parse_args(argv[3:])
        got = render_out(fn(*args, **kwargs))
    except Exception:
        return False
    for t in tests:
        toks = shlex.split(t)
        # test "$out" = 'expected'   /  [ "$out" = 'expected' ]
        if len(toks) >= 4 and toks[0] in ("test", "[") and toks[2] == "=":
            lhs, rhs = toks[1], toks[3]
            val = got if lhs == f"${var}" else lhs
            if val != rhs:
                return False
        else:
            return None                                # 别的写法交给真实 bash
    return True


# ---------------- 沙箱 ----------------
class CliEnv(SynthEnv):
    """SynthEnv + PATH 上加 acmectl."""

    def _env(self):
        env = super()._env()
        env["PATH"] = CLI_DIR + ":" + env["PATH"]
        return env

    def run_sh(self, code, timeout=45):
        import subprocess, tempfile
        fd, path = tempfile.mkstemp(suffix=".sh", dir=self.workdir)
        with os.fdopen(fd, "w") as f:
            f.write(code)
        try:
            p = subprocess.run(["bash", "-e", path], cwd=self.workdir, env=self._env(),     # -e: 任一命令或 test 失败即非零退出
                               capture_output=True, text=True, errors="replace", timeout=timeout)
            return p.returncode == 0, (p.stdout or "") + (p.stderr or "")
        except subprocess.TimeoutExpired:
            return False, "TIMEOUT"


def valid_sh(snippet, mods, seed, month):
    """笔记片段现在还能跑吗: 先进程内模拟, 形态不规范再开真实 bash."""
    r = sim_run_sh(snippet, mods)
    if r is not None:
        return r
    env = CliEnv(seed, month)
    try:
        ok, _ = env.run_sh(snippet)
        return ok
    finally:
        env.cleanup()


# ---------------- 变更日志转写 (Python 措辞 -> 命令行措辞) ----------------
_USAGE = re.compile(r"Usage: `(\w+)\(([^)]*)\)`")
_QUAL = re.compile(r"`(\w+)\.(\w+)`")
_SHAPE = {"a plain value": "a plain JSON value", 'a dict {"value": ...}': 'a JSON object {"value": ...}',
          "a (value, meta) tuple": "a JSON array [value, meta]", "a one-element list": "a one-element JSON array"}


def _usage_cli(sub, name, params):
    parts = []
    for p in [x.strip() for x in params.split(",") if x.strip()]:
        if "=" in p:
            k, v = p.split("=", 1); parts.append(f"[--{k.strip()} {v.strip()}]")
        else:
            parts.append(f"<{p}>")
    return f"acmectl {sub} {name} {' '.join(parts)}".rstrip()


def changelog_cli(text):
    out = []
    for line in text.splitlines():
        m = _QUAL.search(line)
        sub = m.group(1) if m else None
        line = re.sub(r"^# acmesdk ", "# acmectl ", line)
        line = _QUAL.sub(lambda mm: f"`acmectl {mm.group(1)} {mm.group(2)}`", line)
        line = re.sub(r"parameter `(\w+)`", r"flag `--\1`", line)
        line = line.replace("the parameter order changed", "the positional argument order changed")
        for k, v in _SHAPE.items():
            line = line.replace(k, v)
        if sub:
            line = _USAGE.sub(lambda mm: "Usage: `" + _usage_cli(sub, mm.group(1), mm.group(2)) + "`", line)
        out.append(line)
    return "\n".join(out)


# ---------------- 提示词 ----------------
EXAM_SYS = """You maintain legacy shell scripts that call acmectl, an internal
command-line tool of your company. A small script that worked on an older
acmectl release now FAILS on the current release. Repair it so the ORIGINAL
`test` lines pass here.

acmectl is internal: it is not on the public internet and you have no prior
knowledge of it. It has no --help listing, no man page, and its error
messages are generic. Your notes are the only description you have. Trust
them, and verify with a command when unsure.

Hard rules:
- You must NOT modify, remove, or reorder any `test` line: they are the
  spec. Change only the other lines (the acmectl command, its arguments and
  flags, and any post-processing needed so the test still holds). If a note
  tells you the command's output changed (new shape, new unit, new order),
  keep the test line and compensate in the script: unwrap, rescale, reorder,
  or convert the JSON output back to what the test expects, for example with
  `python3 -c` or `sed` in a pipe.
- The repaired script must still call acmectl to do the work (the same
  command or its current replacement). Writing the expected value by hand
  counts as failure.
- Work it out with bash, exactly ONE bash code block with ONE command per
  response; each command costs one unit of a small budget.

When your repaired script passes here (or budget is gone), finish with:

FIXED {"code": "<the full repaired script, original test lines intact>"}"""

EXAM_USER = """Environment: acmectl {ver} (this is the CURRENT version).
The script below was written against acmectl 1.1.0.
{notes}
## Legacy script (fails now)

```bash
{snippet}
```

## Budget

{budget} commands.

Repair it, test it with bash, then hand in the FIXED line."""

REPAIR_SYS = """You maintain a notebook about acmectl, your company's internal
command-line tool, for your future self. One note's example script no longer
runs on the current release. You are given the release notes for that
subsystem since the note was last valid.

Repair the example the way you would repair a legacy script. Keep every
`test` line of the stale example EXACTLY as it is, and change the other lines
so that the test passes on the current release: call the command by its
current name, pass arguments and flags the way it now expects, and if its
output changed (new shape, new unit, new order) add the post-processing that
turns the new output back into the old value. The repaired example is then a
worked recipe for every legacy script that uses this command. In the note
text state what changed and the old-to-new mapping in one or two sentences.

Test with bash first: exactly ONE bash code block with ONE command per
response, each command costs one unit of a small budget.

When the repaired example passes here with the original test lines, finish with:

REPAIRED {"snippet": "<repaired example, original test lines intact>", "text": "<what changed + old-to-new mapping>"}"""

REPAIR_USER = """Environment: acmectl {ver} (CURRENT).

## Your stale note (its test lines are the contract; keep them verbatim)

{note}

## Release notes for acmectl {sub} since your note was last valid

{changelog}
{demo}
## Budget

{budget} commands.

Investigate, then hand in the REPAIRED line."""

REWRITE_SYS = """You maintain a notebook about acmectl, your company's internal
command-line tool, for your future self. One note's example script still runs
on the current release, but the note was patched in a hurry and its
explanation is out of date or missing. Rewrite the note fully.

Keep every `test` line of the example EXACTLY as it is. You may improve the
other lines of the example if that makes it a clearer recipe, but it must
still pass on the current release. In the note text explain, in two or three
sentences, what the command does, how it is called on the current release
(name, arguments, flags, output shape and unit), and what a legacy script
written against acmectl 1.1.0 has to change.

Test with bash first: exactly ONE bash code block with ONE command per
response, each command costs one unit of a small budget.

When the example passes here with the original test lines, finish with:

REPAIRED {"snippet": "<the example, original test lines intact>", "text": "<the rewritten explanation>"}"""

REWRITE_USER = """Environment: acmectl {ver} (CURRENT).

## Your note (its test lines are the contract; keep them verbatim)

{note}

## Release notes for acmectl {sub} since the note was last rewritten

{changelog}

## Budget

{budget} commands.

Verify, then hand in the REPAIRED line."""

TOOL_NAME = "acmectl"
FILES = {"tasks": "tasks_cli.jsonl", "exams": "exams_cli.jsonl", "notes": "notes_cli_M01.jsonl"}
