"""微迁移题挖掘 第一步: 相邻版本环境的 API 差分, 产出断裂点清单.

对每个库, 按数据集的版本轨迹取已建环境, 相邻成对; 在每个环境里用该环境
自己的 python 枚举公开 API (顶层模块 + 一层子模块, 函数/类/方法及其签名),
两两比对得到:
  removed   旧版有、新版没了 (改名或删除)
  sig_diff  两版都有但签名变了
清单落盘 migrations_inventory.jsonl, 供第二步 (LLM 写旧用法片段 + 双环境
机器验证) 出题。全 CPU。
"""
import argparse
import collections
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from world import load_world, GchamEnv, UNPACK                    # noqa: E402

DUMP = r'''
import json, inspect, importlib, pkgutil, sys, warnings
warnings.filterwarnings("ignore")
top = sys.argv[1]
out = {}

def sig(o):
    try:
        base = str(inspect.signature(o))
    except Exception:
        base = "?"
    mod = getattr(o, "__module__", None) or ""
    return base + "|mod=" + mod

def walk(mod, prefix, depth):
    for name in dir(mod):
        if name.startswith("_"):
            continue
        try:
            o = getattr(mod, name)
        except Exception:
            continue
        q = f"{prefix}.{name}"
        if inspect.isfunction(o) or inspect.isbuiltin(o):
            out[q] = sig(o)
        elif inspect.isclass(o):
            out[q] = "class" + sig(o)
            for m in dir(o):
                if m.startswith("_") and m != "__init__":
                    continue
                try:
                    a = getattr(o, m)
                except Exception:
                    continue
                if callable(a):
                    out[f"{q}.{m}"] = sig(a)
        elif inspect.ismodule(o) and depth > 0 \
                and (getattr(o, "__name__", "") or "").startswith(top):
            walk(o, q, depth - 1)

m = importlib.import_module(top)
walk(m, top, 1)
# 显式一层子模块 (dir 看不到未导入的)
if hasattr(m, "__path__"):
    for info in pkgutil.iter_modules(m.__path__):
        if info.name.startswith("_"):
            continue
        try:
            sm = importlib.import_module(f"{top}.{info.name}")
            walk(sm, f"{top}.{info.name}", 0)
        except Exception:
            pass
with open(sys.argv[2], "w") as f:
    json.dump(out, f)
'''

IMPORT_NAME = {"scikit-learn": "sklearn", "pillow": "PIL", "pytest": "pytest",
               "mitmproxy": "mitmproxy"}


def dump_api(env_name, lib, cache_dir):
    cpath = os.path.join(cache_dir, f"api_{env_name}.json")
    if os.path.exists(cpath):
        return json.load(open(cpath))
    env = GchamEnv(env_name)
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".py", dir=env.workdir,
                                         delete=False) as f:
            f.write(DUMP)
            tmp = f.name
        top = IMPORT_NAME.get(lib, lib)
        outp = os.path.join(env.workdir, "api.json")
        cp = subprocess.run([env.python, tmp, top, outp], capture_output=True,
                            text=True, timeout=300, cwd=env.workdir)
        try:
            api = json.load(open(outp))
        except Exception:                                        # noqa: BLE001
            print(f"  dump 失败 {env_name}: {(cp.stderr or cp.stdout or '')[-120:]}")
            return None
        json.dump(api, open(cpath, "w"))
        return api
    finally:
        env.cleanup()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--libs", default="pandas,scipy")
    ap.add_argument("--cache", default=os.environ.get("MIG_API_DUMPS") or os.path.join(
        os.environ.get("MIG_ENVS") or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "mig_envs"),
        "api_dumps"))
    ap.add_argument("--out", default=os.path.join(
        os.path.dirname(HERE),
        "data/gitchameleon/migrations_inventory.jsonl"))
    a = ap.parse_args()
    os.makedirs(a.cache, exist_ok=True)

    probs = load_world()
    libs = a.libs.split(",") if a.libs else sorted({p["library"] for p in probs})
    fh = open(a.out, "a")
    for lib in libs:
        # 该库的版本轨迹 (按发布时间, 去重版本)
        def vkey(v):
            return tuple(int(x) if x.isdigit() else 0 for x in str(v).split("."))
        seen, traj = set(), []
        for p in sorted([x for x in probs if x["library"] == lib],
                        key=lambda x: (vkey(x["version"]), x["release_date"])):
            if p["version"] not in seen:
                seen.add(p["version"])
                traj.append((p["version"], p["release_date"], p["env"]))
        print(f"{lib}: 版本轨迹 {[v for v, _, _ in traj]}")
        apis = {}
        for ver, date, env_name in traj:
            api = dump_api(env_name, lib, a.cache)
            if api:
                apis[ver] = (date, api)
        vers = [v for v, _, _ in traj if v in apis]
        for old, new in zip(vers, vers[1:]):
            d0, a0 = apis[old]
            d1, a1 = apis[new]
            removed = sorted(set(a0) - set(a1))
            sig_diff = sorted(q for q in set(a0) & set(a1)
                              if a0[q] != a1[q] and a0[q] != "?" and a1[q] != "?")
            print(f"  {old} -> {new}: API {len(a0)} -> {len(a1)}, "
                  f"消失 {len(removed)}, 签名变化 {len(sig_diff)}")
            for q in removed:
                fh.write(json.dumps({"lib": lib, "old_ver": old,
                                     "new_ver": new, "date": d1,
                                     "kind": "removed", "api": q,
                                     "old_sig": a0[q]}) + "\n")
            for q in sig_diff:
                fh.write(json.dumps({"lib": lib, "old_ver": old,
                                     "new_ver": new, "date": d1,
                                     "kind": "sig_diff", "api": q,
                                     "old_sig": a0[q], "new_sig": a1[q]}) + "\n")
        fh.flush()
    fh.close()
    print(f"清单 -> {a.out}")


if __name__ == "__main__":
    main()
