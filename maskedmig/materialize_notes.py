"""把 fresh 赛道的笔记物化成目录结构 (用户规范: 模型目录下分 external/internal).

data/maskedmig/<模型名>/
  external/   世界变化记录, 一条一个 .md: 库 X 从 v_old 到 v_new, API 怎么变了,
              证据 (新环境报错)。这是"必须被记录下来 agent 才可能知道"的部分。
  internal/   agent 自己的用法笔记, 一条一个 .md: API 的用法与验证片段。
              出生时记的是旧用法; 若 band_notes 里有重生版, 同名文件存更新版,
              旧版归档为 *.stale.md。
机器可读的 jsonl 保持原位, 目录是给人和 agent 读的视图。
"""
import json
import os
import re

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRESH = os.environ.get("MASKEDMIG_DATA") or os.path.join(_REPO, "data", "maskedmig")
MODEL = "Qwen3.5-9B"


def slug(s):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", s)[:80]


if __name__ == "__main__":   # 只在直接运行时写文件, 避免被 import 时就动手
    ext_dir = f"{FRESH}/{MODEL}/external"
    int_dir = f"{FRESH}/{MODEL}/internal"
    os.makedirs(ext_dir, exist_ok=True)
    os.makedirs(int_dir, exist_ok=True)

    tasks = [json.loads(l) for l in open(f"{FRESH}/fresh_tasks.jsonl")
             if '"task"' in l]
    tasks = [t for t in tasks if t["status"] == "task"]

    band = {}
    bp = f"{FRESH}/band_notes.jsonl"
    if os.path.exists(bp):
        for l in open(bp):
            r = json.loads(l)
            band[(r["lib"], r["api"])] = r

    n_ext = n_int = n_upd = 0
    for t in tasks:
        c = t["cand"]
        name = slug(f"{c['lib']}_{c['api']}")
        # external: 变化记录
        with open(f"{ext_dir}/{name}.md", "w") as f:
            f.write(f"""---
    lib: {c['lib']}
    api: {c['api']}
    change: {c['kind']}
    old_version: {c['old_ver']}
    new_version: {c['new_ver']}
    observed: {c.get('date', '')}
    ---

    {c['api']} 在 {c['lib']} 从 {c['old_ver']} 升到 {c['new_ver']} 后发生
    {'移除或改名' if c['kind'] == 'removed' else '签名变化'}。

    在新版环境中运行旧用法的报错证据:

    ```
    {t['new_err']}
    ```
    """)
        n_ext += 1
        # internal: 用法笔记 (出生=旧用法; 有重生版则更新)
        fr = band.get((c["lib"], c["api"]))
        body_old = f"""---
    lib: {c['lib']}
    api: {c['api']}
    as_of: {c['lib']}=={c['old_ver']}
    ---

    Validated usage of {c['api']}.

    ```python
    {t['snippet']}
    ```
    """
        if fr and fr.get("rebirth_ok"):
            fn = fr["fresh_note"]
            with open(f"{int_dir}/{name}.md", "w") as f:
                f.write(f"""---
    lib: {c['lib']}
    api: {c['api']}
    as_of: {c['lib']}=={c['new_ver']}
    updated_from: {c['old_ver']}
    ---

    {fn['text']}

    ```python
    {fn['claims'][0]['snippet'] if fn['claims'] else ''}
    ```
    """)
            with open(f"{int_dir}/{name}.stale.md", "w") as f:
                f.write(body_old)
            n_upd += 1
        else:
            with open(f"{int_dir}/{name}.md", "w") as f:
                f.write(body_old)
        n_int += 1

    print(f"external {n_ext} 条, internal {n_int} 条 (其中已更新 {n_upd} 条)")
    print(f"-> {FRESH}/{MODEL}/{{external,internal}}/")
