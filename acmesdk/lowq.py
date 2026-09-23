"""低质量笔记设定 (2026-09-18): 固定抽一部分 API, 它们的笔记在考试时只显示名字、不显示用例.
探测/修复读的是存储的 snippet, 不受影响 -> 各条件的有效性完全一致, 只有"有效时的成功率"(质量)不同.
选择规则在这里集中定义, loop.py 与离线模拟共用."""
import random, re

def lowq_ids(seed, ids, frac, mode="random", subs=None):
    """mode=random: 随机抽 frac 的 API. mode=subsystem: 整个子系统一起抽 (笔记质量按部门聚集), 直到达到 frac.
    subs: {note_id: sub}; subsystem 模式必需 (ids 形如 'auth.f001' 时也可从 id 前缀推断)."""
    if frac <= 0: return set()
    rng = random.Random(777 + int(seed))
    ids = sorted(ids); k = int(round(frac * len(ids)))
    if mode == "random":
        return set(rng.sample(ids, k))
    sub_of = subs or {i: i.split(".")[0] for i in ids}
    groups = {}
    for i in ids: groups.setdefault(sub_of[i], []).append(i)
    order = sorted(groups); rng.shuffle(order)
    out = set()
    for g in order:
        if len(out) >= k: break
        out.update(groups[g])
    return out

def degrade_text(note):
    """名字保留, 用例与说明丢弃: 一张写得很差的笔记."""
    # 名字取自已验证的 snippet 的 import (修复后就是当前名字); 找不到再退回 text / api 字段
    m = (re.search(r"from\s+acmesdk\.[\w]+\s+import\s+([\w]+)", note.get("snippet", ""))
         or re.search(r"acmesdk\.[\w]+\.([\w]+)", note.get("text", "")))
    name = m.group(1) if m else note.get("api", "?")
    return (f"acmesdk.{note['sub']} note (terse).\nFunction: {name}. "
            "Usage example not recorded; call it the way the current release expects.")
