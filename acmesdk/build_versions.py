"""把一个种子的世界编译成 36 个版本目录 + 真值 + changelog.

data/acmesdk/seed{k}/
  versions/M01..M36/acmesdk/*.pyc   (只有字节码, 无源码, 表在 blob 里)
  truth/spec_M01..M36.json, events.json, plan.json, apis_M01.json
  changelog/M02..M36.md              (外部知识, 世界发布)
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from kernels import dump_table                    # noqa: E402
from spec import gen_world, SUBSYSTEMS            # noqa: E402
from drift import gen_events, clustering_report   # noqa: E402

# World data. Override with ACMESDK_DATA to keep the (large) worlds outside the repo.
ROOT = os.environ.get("ACMESDK_DATA") or os.path.join(os.path.dirname(HERE), "data", "acmesdk")

SUB_TEMPLATE = '''import sys as _sys
from ._core import make_module as _mm
_sys.modules["__acme_real_" + __name__] = _sys.modules[__name__]
_sys.modules[__name__] = _mm(__name__, {blob!r})
'''

INIT_TEMPLATE = '''from . import {subs}
__all__ = []
__version__ = {ver!r}


def __dir__():
    return []
'''


def write_version(seed_dir, m, state):
    vdir = os.path.join(seed_dir, "versions", f"M{m:02d}")
    pkg = os.path.join(vdir, "acmesdk")
    shutil.rmtree(vdir, ignore_errors=True)
    os.makedirs(pkg)
    shutil.copy(os.path.join(HERE, "kernels.py"), os.path.join(pkg, "_core.py"))
    for sub in SUBSYSTEMS:
        table = {}
        for a in state.values():
            if a["sub"] == sub and a["alive"]:
                table[a["name"]] = {"kernel": a["kernel"], "knobs": a["knobs"],
                                    "shape": a["shape"], "params": a["params"],
                                    "perm": a.get("perm")}
        with open(os.path.join(pkg, sub + ".py"), "w") as f:
            f.write(SUB_TEMPLATE.format(blob=dump_table(table)))
    with open(os.path.join(pkg, "__init__.py"), "w") as f:
        f.write(INIT_TEMPLATE.format(subs=", ".join(SUBSYSTEMS), ver=f"1.{m}.0"))
    # 编译成字节码并删源码 (同一解释器编译与运行)
    subprocess.run([sys.executable, "-m", "compileall", "-b", "-q", pkg], check=True)
    for fn in os.listdir(pkg):
        if fn.endswith(".py"):
            os.remove(os.path.join(pkg, fn))
    shutil.rmtree(os.path.join(pkg, "__pycache__"), ignore_errors=True)
    return vdir


SEMANTIC_TEXT = {
    "unit": lambda o, n: "amounts are now expressed in minor units (x100)" if n == 100
    else "amounts are now expressed in major units (no x100 scaling)",
    "mode": lambda o, n: "returns only the computed part (not the gross total)" if n == "part"
    else "returns the gross total (base plus computed part)",
    "digits": lambda o, n: f"results are now rounded to {n} decimal place(s) (was {o})",
    "round": lambda o, n: "rounding is now banker's rounding" if n == "bankers"
    else "rounding is now half-up",
    "k": lambda o, n: f"the multiplier changed from {o} to {n}",
    "b": lambda o, n: f"the offset changed from {o} to {n}",
    "scale": lambda o, n: f"the output is now scaled by {n} (was {o})",
    "sep": lambda o, n: f"the separator changed from {o!r} to {n!r}",
    "width": lambda o, n: f"the numeric field is now zero-padded to width {n} (was {o})",
    "case": lambda o, n: f"output casing is now {n!r} (was {o!r})",
    "dedupe": lambda o, n: "duplicate items are now removed" if n else "duplicate items are now kept",
    "order": lambda o, n: f"sort order is now {'descending' if n == 'desc' else 'ascending'}",
    "project": lambda o, n: "returns only the key values" if n else "returns the full records",
    "agg": lambda o, n: f"aggregation is now {'the mean' if n == 'mean' else 'the sum'}",
    "missing": lambda o, n: "missing fields are now filled with None" if n == "none"
    else "missing fields are now omitted",
    "fmt": lambda o, n: f"dates are now formatted as {n!r} (was {o!r})",
    "start": lambda o, n: f"weeks now start on {'Monday' if n == 'mon' else 'Sunday'}",
    "mod": lambda o, n: f"the checksum modulus changed from {o} to {n}",
    "hex": lambda o, n: "checksums are now hex strings" if n else "checksums are now integers",
    "right": lambda o, n: "bucket edges are now exclusive (x > edge)" if n
    else "bucket edges are now inclusive (x >= edge)",
    "labels": lambda o, n: f"tier labels are now {n}",
    "as_label": lambda o, n: "returns the tier label" if n else "returns the tier index",
}


def changelog_entry(ev, state_after):
    sub, d, t = ev["sub"], ev["detail"], ev["type"]
    if t == "rename":
        return (f"- `{sub}.{d['old_name']}` was renamed to `{sub}.{d['new_name']}`. "
                f"Usage: `{d['usage']}`")
    if t == "param":
        a = state_after[ev["id"]]
        head = f"- `{sub}.{a['name']}`: "
        if d["op"] == "rename_param":
            body = f"parameter `{d['old_param']}` was renamed to `{d['new_param']}`."
        elif d["op"] == "reorder":
            body = "the parameter order changed."
        else:
            body = f"parameter `{d['param']}` no longer has a default and must be passed."
        return head + body + f" Usage: `{d['usage']}`"
    if t == "shape":
        a = state_after[ev["id"]]
        desc = {"scalar": "a plain value", "dict": 'a dict {"value": ...}',
                "tuple": "a (value, meta) tuple", "list": "a one-element list"}
        return (f"- `{sub}.{a['name']}` now returns {desc[d['new_shape']]} "
                f"(previously {desc[d['old_shape']]}). Usage: `{d['usage']}`")
    if t == "remove":
        return (f"- `{sub}.{d['old_name']}` was removed. Use `{sub}.{d['new_name']}` "
                f"instead. Usage: `{d['usage']}`")
    if t == "semantic":
        a = state_after[ev["id"]]
        text = SEMANTIC_TEXT[d["knob"]](d["old"], d["new"])
        return f"- `{sub}.{a['name']}`: {text}. Usage: `{d['usage']}`"
    raise ValueError(t)


def write_changelogs(seed_dir, events, snapshots, months, silent_layers=()):
    """silent_layers: 这些层的变动不写进发布说明 (如 "kernel": 底层共用实现悄悄改了, 没人记录)."""
    cdir = os.path.join(seed_dir, "changelog")
    shutil.rmtree(cdir, ignore_errors=True)
    os.makedirs(cdir)
    for m in range(2, months + 1):
        evs = [e for e in events if e["m"] == m and e.get("layer") not in silent_layers]
        lines = [f"# acmesdk 1.{m}.0 release notes (M{m:02d})", ""]
        if not evs:
            lines.append("No API changes in this release.")
        for sub in SUBSYSTEMS:
            se = [e for e in evs if e["sub"] == sub]
            if not se:
                continue
            lines.append(f"## {sub}")
            for e in se:
                lines.append(changelog_entry(e, snapshots[m]))
            lines.append("")
        with open(os.path.join(cdir, f"M{m:02d}.md"), "w") as f:
            f.write("\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--months", type=int, default=36)
    ap.add_argument("--bg-rate", type=float, default=0.01)
    ap.add_argument("--n-vertical", type=int, default=6, help="竖向 (子系统) 爆发次数")
    ap.add_argument("--n-horizontal", type=int, default=2, help="横向 (跨子系统) 爆发次数, 最多 2")
    ap.add_argument("--suffix", default="", help="变体世界目录后缀, 如 _noburst; 规格表仍由 --seed 决定")
    ap.add_argument("--burst-frac", default="0.4,0.6", help="一次竖向大改波及部门内函数的比例区间")
    ap.add_argument("--n-kernel", type=int, default=0, help="内核级 (跨部门共因) 变动次数")
    ap.add_argument("--kernel-frac", default="0.8,1.0", help="内核级变动波及该模板函数的比例区间")
    ap.add_argument("--kernel-silent", type=int, default=0, help="1 = 内核级变动不写入发布说明 (静默共因)")
    ap.add_argument("--vmode", default="random", choices=["random", "fixed", "heavytail"],
                    help="大改期排布: random=原行为; fixed=等间隔固定日程; heavytail=少数大改拖 4-9 个月")
    a = ap.parse_args()
    seed_dir = os.path.join(ROOT, f"seed{a.seed}{a.suffix}")
    os.makedirs(os.path.join(seed_dir, "truth"), exist_ok=True)
    apis = gen_world(a.seed)
    bf = tuple(float(x) for x in a.burst_frac.split(","))
    kf = tuple(float(x) for x in a.kernel_frac.split(","))
    events, snapshots, plan = gen_events(apis, a.months, a.seed, bg_rate=a.bg_rate,
                                         n_vertical=a.n_vertical, n_horizontal=a.n_horizontal, burst_frac=bf,
                                         n_kernel=a.n_kernel, kernel_frac=kf, vmode=a.vmode)
    json.dump({"seed": a.seed, "suffix": a.suffix, "months": a.months, "bg_rate": a.bg_rate,
               "n_vertical": a.n_vertical, "n_horizontal": a.n_horizontal, "burst_frac": list(bf), "n_kernel": a.n_kernel, "kernel_frac": list(kf), "kernel_silent": a.kernel_silent, "vmode": a.vmode},
              open(os.path.join(seed_dir, "truth", "world_config.json"), "w"), indent=1)
    json.dump(apis, open(os.path.join(seed_dir, "truth", "apis_M01.json"), "w"), indent=1)
    json.dump(events, open(os.path.join(seed_dir, "truth", "events.json"), "w"), indent=1)
    json.dump(plan, open(os.path.join(seed_dir, "truth", "plan.json"), "w"), indent=1)
    for m in range(1, a.months + 1):
        json.dump(snapshots[m], open(os.path.join(seed_dir, "truth", f"spec_M{m:02d}.json"), "w"))
        write_version(seed_dir, m, snapshots[m])
    write_changelogs(seed_dir, events, snapshots, a.months, silent_layers=("kernel",) if a.kernel_silent else ())
    import collections
    types = collections.Counter(e["type"] for e in events)
    silent = sum(1 for e in events if e["type"] == "semantic")
    cl = clustering_report(events)
    print(f"seed {a.seed}: {len(apis)} 个 API, {len(events)} 个漂移事件, 类型 {dict(types)}")
    print(f"  静默占比 {silent/len(events)*100:.1f}%  爆发计划 {plan}")
    print(f"  聚簇度 (>=4 事件的月份): 平均 {sum(s for _,_,s in cl)/len(cl)*100:.0f}%  "
          f"明细 {[(m, n, round(s,2)) for m, n, s in cl]}")
    print(f"  写出 {seed_dir}")


if __name__ == "__main__":
    main()
