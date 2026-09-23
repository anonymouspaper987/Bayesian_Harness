"""acmesdk 运行时内核 + 黑箱外壳.

这个文件会被原样拷成每个版本目录里的 acmesdk/_core.py 并编译成字节码.
生成器也直接 import 它来算考题的期望输出, 保证两边语义一致.

约定:
- 每个 API = 内核名 + 旋钮 (knobs) + 参数表 (params) + 返回外形 (shape).
- 旋钮里带 "语义" 的那些翻一下就是静默漂移 (不报错, 结果变).
- 外壳只暴露 (*args, **kwargs), 参数绑定按隐藏参数表做, 报错一律泛化.
"""
import base64
import datetime as _dt
import json
import math
import zlib

REQUIRED = "__required__"


# ---------------- 内核: 纯函数, 由 knobs 参数化 ----------------
def _round(x, digits, mode):
    if mode == "bankers":
        return round(x, digits)
    q = 10 ** digits
    return math.floor(x * q + 0.5) / q if x >= 0 else -math.floor(-x * q + 0.5) / q


def k_linear(kn, x):
    v = x * kn["k"] + kn["b"]
    v = v * kn.get("unit", 1)
    return _round(v, kn["digits"], kn["round"])


def k_percent(kn, amount, rate):
    v = amount * (1 + rate) if kn["mode"] == "gross" else amount * rate
    v = v * kn.get("unit", 1)
    return _round(v, kn["digits"], kn["round"])


def k_clamp(kn, x, lo, hi):
    v = min(max(x, lo), hi)
    if kn["scale"] != 1:
        v = v * kn["scale"]
    return _round(v, kn["digits"], "bankers")


def k_fmt_id(kn, prefix, n):
    s = f"{prefix}{kn['sep']}{n:0{kn['width']}d}"
    return s.upper() if kn["case"] == "upper" else s.lower()


def k_join(kn, items):
    seq = list(items)
    if kn["dedupe"]:
        seen, out = set(), []
        for it in seq:
            if it not in seen:
                seen.add(it)
                out.append(it)
        seq = out
    s = kn["sep"].join(str(i) for i in seq)
    return {"none": s, "upper": s.upper(), "title": s.title()}[kn["case"]]


def k_sort_top(kn, records, key, k):
    rs = sorted(records, key=lambda r: r[key], reverse=(kn["order"] == "desc"))
    rs = rs[:k]
    return [r[key] for r in rs] if kn["project"] else rs


def k_aggregate(kn, values):
    vs = list(values)
    if not vs:
        return 0
    v = sum(vs) if kn["agg"] == "sum" else sum(vs) / len(vs)
    v = v * kn.get("unit", 1)
    return _round(v, kn["digits"], kn["round"])


def k_pick(kn, record, fields):
    out = {}
    for f in fields:
        if f in record:
            out[f] = record[f]
        elif kn["missing"] == "none":
            out[f] = None
    return out


def k_date_shift(kn, date_str, days):
    d = _dt.date.fromisoformat(date_str) + _dt.timedelta(days=days)
    return d.strftime(kn["fmt"])


def k_week_start(kn, date_str):
    d = _dt.date.fromisoformat(date_str)
    off = d.weekday() if kn["start"] == "mon" else (d.weekday() + 1) % 7
    return (d - _dt.timedelta(days=off)).isoformat()


def k_checksum(kn, s):
    v = sum((i + 1) * ord(c) for i, c in enumerate(s)) % kn["mod"]
    return format(v, "x") if kn["hex"] else v


def k_bucket(kn, x, edges):
    i = 0
    for e in edges:
        if (x > e) if kn["right"] else (x >= e):
            i += 1
    return i


def k_tier(kn, amount, thresholds):
    tier = 0
    for t in thresholds:
        if amount >= t:
            tier += 1
    label = kn["labels"][min(tier, len(kn["labels"]) - 1)]
    return label if kn["as_label"] else tier


KERNELS = {
    "linear": k_linear, "percent": k_percent, "clamp": k_clamp,
    "fmt_id": k_fmt_id, "join": k_join, "sort_top": k_sort_top,
    "aggregate": k_aggregate, "pick": k_pick, "date_shift": k_date_shift,
    "week_start": k_week_start, "checksum": k_checksum, "bucket": k_bucket,
    "tier": k_tier,
}

# 内核参数 (按顺序) 与类型; 参数在 API 规格里可以被改名/改序/改必填
KERNEL_PARAMS = {
    "linear": [("x", "number")],
    "percent": [("amount", "number"), ("rate", "number")],
    "clamp": [("x", "number"), ("lo", "number"), ("hi", "number")],
    "fmt_id": [("prefix", "str"), ("n", "int")],
    "join": [("items", "list")],
    "sort_top": [("records", "list"), ("key", "str"), ("k", "int")],
    "aggregate": [("values", "list")],
    "pick": [("record", "dict"), ("fields", "list")],
    "date_shift": [("date_str", "str"), ("days", "int")],
    "week_start": [("date_str", "str")],
    "checksum": [("s", "str")],
    "bucket": [("x", "number"), ("edges", "list")],
    "tier": [("amount", "number"), ("thresholds", "list")],
}


def shape_wrap(shape, v, name):
    if shape == "scalar":
        return v
    if shape == "dict":
        return {"value": v}
    if shape == "tuple":
        return (v, {"source": name})
    if shape == "list":
        return [v]
    raise ValueError(shape)


def _kind_ok(kind, v):
    if kind == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if kind == "int":
        return isinstance(v, int) and not isinstance(v, bool)
    if kind == "str":
        return isinstance(v, str)
    if kind == "list":
        return isinstance(v, (list, tuple))
    if kind == "dict":
        return isinstance(v, dict)
    return True


def call_api(name, spec, args, kwargs):
    """黑箱调用: 隐藏参数表绑定 + 内核执行 + 外形包装. 报错全部泛化."""
    params = spec["params"]          # [{"n": 公开名, "k": 类型, "d": 默认}]
    if len(args) > len(params):
        raise TypeError(f"invalid arguments for {name}")
    bound = {}
    for p, a in zip(params, args):
        bound[p["n"]] = a
    for kname, v in kwargs.items():
        if kname in bound or kname not in {p["n"] for p in params}:
            raise TypeError(f"invalid arguments for {name}")
        bound[kname] = v
    for p in params:
        if p["n"] not in bound:
            if "d" in p:
                bound[p["n"]] = p["d"]
            else:
                raise TypeError(f"invalid arguments for {name}")
        if not _kind_ok(p["k"], bound[p["n"]]):
            raise TypeError(f"invalid arguments for {name}")
    # 公开参数 -> 内核形参: 按 perm 置换 (改序漂移后位置不再一一对应)
    kernel_params = KERNEL_PARAMS[spec["kernel"]]
    core = [p for p in params if not p.get("extra")]
    perm = spec.get("perm") or list(range(len(core)))
    kargs = {kernel_params[perm[i]][0]: bound[p["n"]] for i, p in enumerate(core)}
    v = KERNELS[spec["kernel"]](spec["knobs"], **kargs)
    return shape_wrap(spec["shape"], v, name)


def public_kwargs(spec, kernel_inputs, token="tok"):
    """内核形参取值 -> 按当前规格的公开参数名 (含 perm 与附加参数)."""
    kernel_params = KERNEL_PARAMS[spec["kernel"]]
    core = [p for p in spec["params"] if not p.get("extra")]
    perm = spec.get("perm") or list(range(len(core)))
    kw = {p["n"]: kernel_inputs[kernel_params[perm[i]][0]] for i, p in enumerate(core)}
    for p in spec["params"]:
        if p.get("extra"):
            kw[p["n"]] = token
    return kw


# ---------------- 黑箱模块外壳 ----------------
def dump_table(table):
    raw = json.dumps(table, sort_keys=True).encode()
    return base64.b85encode(zlib.compress(raw, 9)).decode()


def load_table(blob):
    return json.loads(zlib.decompress(base64.b85decode(blob.encode())))


def make_module(modname, blob):
    """把子系统模块替换成代理对象: 目录为空, 未知名字泛化报错, 表在闭包里."""
    import sys
    table = load_table(blob)
    def _mk(fname, spec):
        def _w(*args, **kwargs):
            return call_api(fname, spec, args, kwargs)
        _w.__name__ = fname
        _w.__qualname__ = fname
        _w.__module__ = modname
        return _w

    funcs = {fname: _mk(fname, spec) for fname, spec in table.items()}

    class _Proxy:
        __slots__ = ()

        def __getattr__(self, name):
            if name in funcs:
                return funcs[name]
            if name in ("__name__", "__spec__", "__loader__", "__package__",
                        "__file__", "__path__"):
                return getattr(sys.modules.get("__acme_real_" + modname), name, None)
            raise AttributeError("unknown attribute")

        def __dir__(self):
            return []

        def __repr__(self):
            return f"<module '{modname}'>"

    return _Proxy()
