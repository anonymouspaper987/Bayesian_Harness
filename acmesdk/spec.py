"""acmesdk API 规格生成器 (按种子确定性生成 ~400 个 API 的 M01 状态).

每个 API 规格:
  {"id": "billing.f017", "sub": "billing", "name": "compute_tax",
   "kernel": "percent", "knobs": {...}, "shape": "scalar",
   "params": [{"n": "amount", "k": "number"}, {"n": "rate", "k": "number", "d": 0.2}],
   "tags": ["money"], "alive": true, "succ": null}
"""
import random

from kernels import KERNEL_PARAMS, REQUIRED  # noqa: F401

SUBSYSTEMS = {
    "billing":   ["tax", "discount", "invoice", "fee", "refund", "surcharge", "credit", "charge"],
    "auth":      ["token", "session", "grant", "scope", "nonce", "claim", "policy", "role"],
    "storage":   ["blob", "bucket", "object", "chunk", "manifest", "snapshot", "quota", "path"],
    "search":    ["query", "hit", "facet", "ranking", "index", "term", "snippet", "cursor"],
    "notify":    ["alert", "digest", "channel", "template", "recipient", "batch", "receipt", "topic"],
    "ledger":    ["entry", "balance", "posting", "period", "journal", "account", "settlement", "reserve"],
    "catalog":   ["sku", "listing", "variant", "price", "bundle", "category", "stock", "label"],
    "shipping":  ["parcel", "route", "rate", "zone", "weight", "eta", "carrier", "manifest"],
    "identity":  ["user", "profile", "handle", "avatar", "alias", "tenant", "group", "badge"],
    "metrics":   ["counter", "gauge", "window", "sample", "series", "percentile", "bucket", "rollup"],
    "scheduler": ["job", "slot", "cron", "retry", "deadline", "shift", "window", "lease"],
    "export":    ["report", "sheet", "column", "row", "archive", "format", "header", "page"],
}

MONEY_SUBS = {"billing", "ledger", "catalog", "shipping"}

NUM_KERNELS = ["linear", "percent", "clamp", "aggregate", "tier", "bucket"]
STR_KERNELS = ["fmt_id", "join", "checksum", "date_shift", "week_start"]
COL_KERNELS = ["sort_top", "pick"]

VERBS = {
    "num": [["compute", "calc", "calculate", "derive"], ["apply", "add", "attach"],
            ["total", "sum", "accumulate"], ["estimate", "quote", "project"],
            ["settle", "close", "finalize"], ["assess", "grade", "score"],
            ["scale", "adjust", "normalize"], ["rate", "price", "cost"]],
    "str": [["format", "render", "fmt"], ["build", "make", "construct"],
            ["encode", "hash", "sign"], ["compose", "assemble", "concat"],
            ["shift", "move", "roll"], ["label", "tag", "name"]],
    "col": [["select", "choose", "pick"], ["rank", "order", "sort"],
            ["top", "head", "best"], ["extract", "pull", "take"],
            ["filter", "trim", "prune"], ["collect", "gather", "group"]],
}

PARAM_ALIASES = {
    "x": ["x", "value", "amount", "input"], "amount": ["amount", "value", "price", "base"],
    "rate": ["rate", "pct", "ratio", "factor"], "lo": ["lo", "floor", "minimum"],
    "hi": ["hi", "ceiling", "maximum"], "prefix": ["prefix", "kind", "code"],
    "n": ["n", "seq", "number", "index"], "items": ["items", "parts", "tokens", "words"],
    "records": ["records", "rows", "entries", "items"], "key": ["key", "field", "by"],
    "k": ["k", "limit", "n", "count"], "values": ["values", "amounts", "series", "samples"],
    "record": ["record", "row", "item", "payload"], "fields": ["fields", "keys", "columns"],
    "date_str": ["date", "day", "on", "when"], "days": ["days", "offset", "delta"],
    "s": ["text", "payload", "s", "data"], "edges": ["edges", "cuts", "bounds"],
    "thresholds": ["thresholds", "levels", "tiers", "steps"],
}

KNOB_SPACE = {
    "linear":     {"k": [0.5, 1.5, 2, 2.5, 3], "b": [0, 1, 5, 10], "digits": [0, 1, 2],
                   "round": ["half_up", "bankers"]},
    "percent":    {"mode": ["gross", "part"], "digits": [0, 1, 2], "round": ["half_up", "bankers"]},
    "clamp":      {"scale": [1, 10], "digits": [0, 1, 2]},
    "fmt_id":     {"sep": ["-", "_", ":", ""], "width": [4, 6, 8], "case": ["upper", "lower"]},
    "join":       {"sep": [", ", " | ", "/", " "], "case": ["none", "upper", "title"], "dedupe": [True, False]},
    "sort_top":   {"order": ["asc", "desc"], "project": [True, False]},
    "aggregate":  {"agg": ["sum", "mean"], "digits": [0, 1, 2], "round": ["half_up", "bankers"]},
    "pick":       {"missing": ["none", "skip"]},
    "date_shift": {"fmt": ["%Y-%m-%d", "%d/%m/%Y", "%Y%m%d"]},
    "week_start": {"start": ["mon", "sun"]},
    "checksum":   {"mod": [97, 251, 1009], "hex": [True, False]},
    "bucket":     {"right": [True, False]},
    "tier":       {"labels": [["bronze", "silver", "gold"], ["basic", "plus", "pro"],
                              ["low", "mid", "high"]], "as_label": [True, False]},
}

# 默认值候选 (有默认值的参数才能被 "改必填" 漂移击中)
DEFAULTS = {"rate": [0.1, 0.2, 0.25], "k": [3, 5], "days": [7, 30], "fields": None}


def kernel_family(kernel):
    if kernel in NUM_KERNELS:
        return "num"
    if kernel in STR_KERNELS:
        return "str"
    return "col"


def gen_world(seed, n_per_sub=(30, 40)):
    rng = random.Random(seed)
    apis = []
    for sub, nouns in SUBSYSTEMS.items():
        n = rng.randint(*n_per_sub)
        used = set()
        idx = 0
        while len([a for a in apis if a["sub"] == sub]) < n:
            fam = rng.choices(["num", "str", "col"], weights=[5, 3, 2])[0]
            kernel = rng.choice({"num": NUM_KERNELS, "str": STR_KERNELS,
                                 "col": COL_KERNELS}[fam])
            vgroup = rng.choice(VERBS[fam])
            name = f"{vgroup[0]}_{rng.choice(nouns)}"
            if name in used:
                name = f"{vgroup[0]}_{rng.choice(nouns)}_{rng.choice(['v', 'x', 'by', 'for'])}"
                if name in used:
                    continue
            used.add(name)
            idx += 1
            knobs = {kn: rng.choice(opts) for kn, opts in KNOB_SPACE[kernel].items()}
            tags = []
            if kernel in ("linear", "percent", "aggregate") and sub in MONEY_SUBS:
                knobs["unit"] = 1
                tags.append("money")
            params = []
            for kp, kind in KERNEL_PARAMS[kernel]:
                p = {"n": rng.choice(PARAM_ALIASES[kp]), "k": kind}
                if kp in DEFAULTS and DEFAULTS[kp] and rng.random() < 0.7:
                    p["d"] = rng.choice(DEFAULTS[kp])
                params.append(p)
            # 公开名不能重复
            seen = set()
            for p in params:
                while p["n"] in seen:
                    p["n"] = p["n"] + "_"
                seen.add(p["n"])
            if rng.random() < 0.15:
                params.append({"n": "token", "k": "str", "extra": True})
                tags.append("auth")
            shape = rng.choices(["scalar", "dict", "tuple", "list"],
                                weights=[6, 2, 1, 1])[0]
            apis.append({"id": f"{sub}.f{idx:03d}", "sub": sub, "name": name,
                         "kernel": kernel, "knobs": knobs, "shape": shape,
                         "params": params, "tags": tags, "alive": True,
                         "succ": None, "vgroup": vgroup})
    return apis


# ---------------- 考题输入采样 ----------------
WORDS = ["alpha", "bravo", "delta", "echo", "kilo", "lima", "oscar", "tango",
         "zulu", "nova", "pixel", "quartz", "ember", "flint", "harbor"]


def sample_inputs(kernel, rng):
    """返回 {内核形参: 值}; 值全是可 repr 的 python 字面量."""
    if kernel == "linear":
        return {"x": rng.choice([12, 25.5, 40, 99.99, 150, 7.25])}
    if kernel == "percent":
        return {"amount": rng.choice([80, 120.0, 250, 999.5, 1500]),
                "rate": rng.choice([0.1, 0.15, 0.2, 0.3])}
    if kernel == "clamp":
        lo = rng.choice([0, 10, 50])
        return {"x": rng.choice([-5, 25, 75, 120]), "lo": lo, "hi": lo + rng.choice([40, 60])}
    if kernel == "fmt_id":
        return {"prefix": rng.choice(["INV", "ord", "Tk", "usr"]), "n": rng.choice([7, 42, 1234, 98765])}
    if kernel == "join":
        ws = rng.sample(WORDS, 4)
        if rng.random() < 0.6:
            ws.append(ws[1])
        return {"items": ws}
    if kernel == "sort_top":
        vals = rng.sample(range(1, 60), 5)
        return {"records": [{"id": i, "score": v} for i, v in enumerate(vals)],
                "key": "score", "k": rng.choice([2, 3])}
    if kernel == "aggregate":
        return {"values": [rng.choice([3, 4.5, 10, 12.25, 20, 31]) for _ in range(rng.choice([3, 4, 5]))]}
    if kernel == "pick":
        return {"record": {"id": rng.randint(1, 99), "name": rng.choice(WORDS),
                           "tier": rng.choice(["a", "b"])},
                "fields": ["id", "name", rng.choice(["region", "owner"])]}
    if kernel == "date_shift":
        return {"date_str": rng.choice(["2024-03-15", "2025-01-31", "2023-12-29"]),
                "days": rng.choice([3, 10, 45, -7])}
    if kernel == "week_start":
        return {"date_str": rng.choice(["2024-03-13", "2025-06-01", "2023-11-25"])}
    if kernel == "checksum":
        return {"s": rng.choice(WORDS) + rng.choice(["-1", "_x", "42"])}
    if kernel == "bucket":
        return {"x": rng.choice([5, 10, 25, 50, 75]), "edges": [10, 25, 50]}
    if kernel == "tier":
        return {"amount": rng.choice([50, 100, 300, 500, 900]), "thresholds": [100, 500]}
    raise KeyError(kernel)
