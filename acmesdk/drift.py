"""三层漂移事件生成 (背景涓流 + 竖向爆发 + 横向爆发), 逐月应用到规格状态.

事件 = {"m": 月, "id": api_id, "type": 类型, "sub": 子系统, "detail": {...}}
类型: rename / param / shape / remove / semantic
"""
import copy
import random

from kernels import call_api, public_kwargs, KERNEL_PARAMS
from spec import KNOB_SPACE, PARAM_ALIASES, VERBS, sample_inputs, kernel_family

TYPE_WEIGHTS = {"rename": 30, "param": 25, "shape": 15, "remove": 10, "semantic": 20}
SHAPES = ["scalar", "dict", "tuple", "list"]


def _usage(spec):
    parts = []
    for p in spec["params"]:
        parts.append(f"{p['n']}={p['d']!r}" if "d" in p else p["n"])
    return f"{spec['name']}({', '.join(parts)})"


def _unique_name(state, sub, cands, current, hist=()):
    """子系统内唯一, 且不复用该 API 历史上用过的名字 (防过期笔记诈尸)."""
    taken = {a["name"] for a in state.values() if a["sub"] == sub and a["alive"]}
    taken |= set(hist)
    for c in cands:
        if c != current and c not in taken:
            return c
    return None


def _rename_candidates(spec, rng):
    verb, noun_rest = spec["name"].split("_", 1)
    group = spec.get("vgroup") or next((g for g in VERBS[kernel_family(spec["kernel"])]
                                        if verb in g), [verb])
    cands = [f"{v}_{noun_rest}" for v in group if v != verb]
    rng.shuffle(cands)
    # 兜底: 换个动词组
    for g in VERBS[kernel_family(spec["kernel"])]:
        if g is not group:
            cands.append(f"{g[0]}_{noun_rest}")
    return cands


def _outputs(spec, rng_seed=7):
    """3 组采样输入下的输出, 用来判断语义翻转是否真的改变结果."""
    rng = random.Random(rng_seed)
    outs = []
    for _ in range(3):
        ins = sample_inputs(spec["kernel"], rng)
        kw = public_kwargs(spec, ins)
        try:
            outs.append(repr(call_api(spec["name"], spec, (), kw)))
        except Exception as e:
            outs.append("ERR:" + type(e).__name__)
    return outs


def apply_event(state, ev, rng):
    """就地修改 state; 返回 detail (供 changelog), 不可行返回 None."""
    a = state[ev["id"]]
    t = ev["type"]
    if t == "rename":
        new = _unique_name(state, a["sub"], _rename_candidates(a, rng), a["name"],
                           a.get("hist", []))
        if not new:
            return None
        d = {"old_name": a["name"], "new_name": new}
        a.setdefault("hist", []).append(a["name"])
        a["name"] = new
        d["usage"] = _usage(a)
        return d
    if t == "param":
        core = [p for p in a["params"] if not p.get("extra")]
        opts = []
        if core:
            opts.append("rename_param")
        if len(core) >= 2:
            opts.append("reorder")
        if any("d" in p for p in core):
            opts.append("require")
        if not opts:
            return None
        op = rng.choice(opts)
        if op == "rename_param":
            i = rng.randrange(len(core))
            perm = a.get("perm") or list(range(len(core)))
            kp = KERNEL_PARAMS[a["kernel"]][perm[i]][0]
            taken = {p["n"] for p in a["params"]}
            cands = [x for x in PARAM_ALIASES[kp] if x not in taken]
            if not cands:
                return None
            new = rng.choice(cands)
            d = {"op": op, "old_param": core[i]["n"], "new_param": new}
            core[i]["n"] = new
        elif op == "reorder":
            i, j = 0, 1
            d = {"op": op, "params_before": [p["n"] for p in a["params"]]}
            # 交换公开参数表里前两个核心参数 (内核形参顺序跟着走)
            idx = [k for k, p in enumerate(a["params"]) if not p.get("extra")]
            a["params"][idx[i]], a["params"][idx[j]] = a["params"][idx[j]], a["params"][idx[i]]
            # 内核形参与公开参数按位置对应, 记录一个置换让 call_api 能还原
            perm = a.get("perm") or list(range(len(idx)))
            perm[i], perm[j] = perm[j], perm[i]
            a["perm"] = perm
        else:
            cands = [p for p in core if "d" in p]
            p = rng.choice(cands)
            d = {"op": op, "param": p["n"], "old_default": p["d"]}
            del p["d"]
        d["usage"] = _usage(a)
        return d
    if t == "shape":
        new = rng.choice([s for s in SHAPES if s != a["shape"]])
        d = {"old_shape": a["shape"], "new_shape": new, "usage": _usage(a)}
        a["shape"] = new
        return d
    if t == "remove":
        new = _unique_name(state, a["sub"], _rename_candidates(a, rng), a["name"],
                           a.get("hist", []))
        if not new:
            return None
        succ = copy.deepcopy(a)
        succ["id"] = a["id"] + "s"
        succ["name"] = new
        succ["hist"] = a.get("hist", []) + [a["name"]]
        succ["succ"] = None
        state[succ["id"]] = succ
        a["alive"] = False
        a["succ"] = succ["id"]
        return {"old_name": a["name"], "succ_id": succ["id"], "new_name": new,
                "usage": _usage(succ)}
    if t == "semantic":
        before = _outputs(a)
        # 不可逆的旋钮不做静默漂移 (旧断言无法通过任何补偿代码恢复)
        knobs = [(kn, opts) for kn, opts in KNOB_SPACE[a["kernel"]].items()
                 if kn not in ("mod", "right", "dedupe")]
        if "unit" in a["knobs"]:
            knobs.append(("unit", [1, 100]))
        rng.shuffle(knobs)
        for kn, opts in knobs:
            alts = [o for o in opts if o != a["knobs"].get(kn)]
            if not alts:
                continue
            old = a["knobs"].get(kn)
            a["knobs"][kn] = rng.choice(alts)
            if _outputs(a) != before:
                return {"knob": kn, "old": old, "new": a["knobs"][kn], "usage": _usage(a)}
            a["knobs"][kn] = old
        return None
    raise ValueError(t)


def gen_events(apis, months, seed, bg_rate=0.01, n_vertical=6, n_horizontal=2,
               max_per_api=3, burst_frac=(0.4, 0.6), n_kernel=0, kernel_frac=(0.8, 1.0),
               vmode="random"):
    rng = random.Random(seed * 7919 + 1)
    state = {a["id"]: copy.deepcopy(a) for a in apis}
    changes = {a["id"]: 0 for a in apis}
    snapshots = {1: copy.deepcopy(state)}
    events = []
    subs = sorted({a["sub"] for a in apis})
    # 爆发月份: 竖向 6 次彼此至少隔 3 个月, 横向 2 次, 都避开前 3 个月
    slots = list(range(4, months + 1))
    rng.shuffle(slots)
    vmonths = []
    vgap = 3 if n_vertical <= 8 else 2          # 密集变体允许更近的爆发
    for s in slots:
        if len(vmonths) >= n_vertical:
            break
        if all(abs(s - v) >= vgap for v in vmonths):
            vmonths.append(s)
    vsubs = rng.sample(subs, n_vertical)
    # vmode 决定大改期怎么排 (2026-09-18, 回应"生成器结构就是模型假设"的质疑):
    #   random    = 原行为, 起始月随机, 每次只持续一个 release
    #   fixed     = 等间隔固定日程, 不是随机切换, 违反模型的 lambda (进入大改的速率) 假设
    #   heavytail = 多数大改一个月, 少数连着拖 4-9 个月, 违反模型"大改约一个 release"的
    #               指数时长假设 (mu=1)
    if vmode == "fixed":
        step = max(2, (months - 4) // max(n_vertical, 1))
        vmonths = [m for m in (4 + i * step for i in range(n_vertical)) if m <= months]
    if vmode == "heavytail":
        rng_t = random.Random(seed * 104729 + 7)
        vplan = {}
        for start, sub in zip(sorted(vmonths), vsubs):
            span = 1 if rng_t.random() < 0.6 else rng_t.randint(4, 9)
            for d in range(span):
                if start + d <= months:
                    vplan.setdefault(start + d, sub)
    else:
        vplan = dict(zip(sorted(vmonths), vsubs))
    hmonths = []
    for s in slots:
        if len(hmonths) >= n_horizontal:
            break
        if s not in vplan and all(abs(s - h) >= 6 for h in hmonths):
            hmonths.append(s)
    hplan = dict(zip(sorted(hmonths), ["auth_param", "money_unit"]))
    # 内核级变动 (2026-09-06): 某个共用的内核模板改了, 用它的函数不分部门一起变. 这是不在文档里声明的共因.
    kmonths = []
    for s_ in slots:
        if len(kmonths) >= n_kernel:
            break
        if s_ not in vplan and s_ not in hmonths and all(abs(s_ - k) >= 2 for k in kmonths):
            kmonths.append(s_)
    kernels = sorted({a["kernel"] for a in apis})
    kkinds = ["shape", "param", "semantic"]
    kplan = {m: (k, rng.choice(kkinds)) for m, k in zip(sorted(kmonths), rng.sample(kernels, min(n_kernel, len(kernels))))}

    def emit(m, aid, t, layer="background"):
        if changes[aid] >= max_per_api or not state[aid]["alive"]:
            return False
        ev = {"m": m, "id": aid, "type": t, "sub": state[aid]["sub"], "layer": layer}
        d = apply_event(state, ev, rng)
        if d is None:
            return False
        ev["detail"] = d
        events.append(ev)
        changes[aid] += 1
        if t == "remove":
            changes[d["succ_id"]] = changes[aid]
        return True

    for m in range(2, months + 1):
        touched = set()
        alive = [aid for aid, a in state.items() if a["alive"]]
        # 竖向爆发
        if m in vplan:
            sub = vplan[m]
            pool = [aid for aid in alive if state[aid]["sub"] == sub]
            k = int(len(pool) * rng.uniform(*burst_frac))     # 一次大改波及部门内函数的比例
            for aid in rng.sample(pool, k):
                t = rng.choices(list(TYPE_WEIGHTS), weights=list(TYPE_WEIGHTS.values()))[0]
                if emit(m, aid, t, layer="vertical"):
                    touched.add(aid)
        # 横向爆发
        if m in hplan:
            kind = hplan[m]
            for aid in alive:
                a = state[aid]
                if aid in touched:
                    continue
                if kind == "auth_param" and "auth" in a["tags"]:
                    tok = next((p for p in a["params"] if p.get("extra")), None)
                    if tok and tok["n"] == "token" and changes[aid] < max_per_api:
                        tok["n"] = "credential"
                        events.append({"m": m, "id": aid, "type": "param", "sub": a["sub"],
                                       "detail": {"op": "rename_param", "old_param": "token",
                                                  "new_param": "credential", "usage": _usage(a),
                                                  "horizontal": True}, "layer": "horizontal"})
                        changes[aid] += 1
                        touched.add(aid)
                if kind == "money_unit" and "money" in a["tags"] and a["knobs"].get("unit") == 1 \
                        and changes[aid] < max_per_api:
                    a["knobs"]["unit"] = 100
                    events.append({"m": m, "id": aid, "type": "semantic", "sub": a["sub"],
                                   "detail": {"knob": "unit", "old": 1, "new": 100,
                                              "usage": _usage(a), "horizontal": True},
                                   "layer": "horizontal"})
                    changes[aid] += 1
                    touched.add(aid)
        # 内核级变动: 同一模板的函数一起变 (跨部门), 同一种改法
        if m in kplan:
            K, kind = kplan[m]
            pool = [aid for aid in alive if state[aid]["kernel"] == K and aid not in touched]
            pool = rng.sample(pool, int(len(pool) * rng.uniform(*kernel_frac)))
            if kind == "shape":
                target = rng.choice(SHAPES)
                for aid in pool:
                    a = state[aid]
                    if a["shape"] == target or changes[aid] >= max_per_api:
                        continue
                    events.append({"m": m, "id": aid, "type": "shape", "sub": a["sub"], "layer": "kernel", "kernel": K,
                                   "detail": {"old_shape": a["shape"], "new_shape": target, "usage": _usage(a)}})
                    a["shape"] = target; changes[aid] += 1; touched.add(aid)
            elif kind == "param":
                slot = rng.randrange(len(KERNEL_PARAMS[K]))
                kp = KERNEL_PARAMS[K][slot][0]
                for aid in pool:
                    a = state[aid]
                    if changes[aid] >= max_per_api:
                        continue
                    core = [p for p in a["params"] if not p.get("extra")]
                    perm = a.get("perm") or list(range(len(core)))
                    try:
                        i = perm.index(slot)
                    except ValueError:
                        continue
                    taken = {p["n"] for p in a["params"]}
                    cands = [x for x in PARAM_ALIASES[kp] if x not in taken]
                    if not cands:
                        continue
                    new = rng.choice(cands)
                    events.append({"m": m, "id": aid, "type": "param", "sub": a["sub"], "layer": "kernel", "kernel": K,
                                   "detail": {"op": "rename_param", "old_param": core[i]["n"], "new_param": new}})
                    core[i]["n"] = new; events[-1]["detail"]["usage"] = _usage(a); changes[aid] += 1; touched.add(aid)
            else:
                knobs = [(kn, opts) for kn, opts in KNOB_SPACE[K].items() if kn not in ("mod", "right", "dedupe")]
                if knobs:
                    kn, opts = rng.choice(knobs)
                    for aid in pool:
                        a = state[aid]
                        if changes[aid] >= max_per_api:
                            continue
                        alts = [o for o in opts if o != a["knobs"].get(kn)]
                        if not alts:
                            continue
                        before = _outputs(a); old = a["knobs"].get(kn); a["knobs"][kn] = rng.choice(alts)
                        if _outputs(a) == before:
                            a["knobs"][kn] = old; continue
                        events.append({"m": m, "id": aid, "type": "semantic", "sub": a["sub"], "layer": "kernel", "kernel": K,
                                       "detail": {"knob": kn, "old": old, "new": a["knobs"][kn], "usage": _usage(a)}})
                        changes[aid] += 1; touched.add(aid)
        # 背景涓流
        for aid in alive:
            if aid in touched or not state[aid]["alive"]:
                continue
            if rng.random() < bg_rate:
                t = rng.choices(list(TYPE_WEIGHTS), weights=list(TYPE_WEIGHTS.values()))[0]
                emit(m, aid, t)
        snapshots[m] = copy.deepcopy(state)
    return events, snapshots, {"vertical": vplan, "horizontal": hplan, "kernel": {m: list(v) for m, v in kplan.items()}}


def clustering_report(events, min_events=4):
    import collections
    by_m = collections.defaultdict(list)
    for e in events:
        by_m[e["m"]].append(e["sub"])
    shares = []
    for m, subs in sorted(by_m.items()):
        if len(subs) >= min_events:
            top = collections.Counter(subs).most_common(1)[0][1]
            shares.append((m, len(subs), top / len(subs)))
    return shares
