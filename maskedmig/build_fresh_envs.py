"""后截止期版本对的环境构建: 每对两个 venv 打 tar.

沿用全部构建教训: 先 rm -rf 防残留; UV_LINK_MODE=copy; exclude-newer 取
该版本发布日 +45 天 (保时代一致); 预种 setuptools/wheel + no-build-isolation;
uv 失败落 pip 通道; 验证 = import 顶层模块且 __version__ 前缀吻合。
"""
import concurrent.futures as cf
import datetime
import json
import os
import tempfile
import subprocess
import time

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FRESH = os.environ.get("MASKEDMIG_DATA") or os.path.join(_REPO, "data", "maskedmig")
_ENVS = os.environ.get("MIG_ENVS") or os.path.join(_REPO, "data", "mig_envs")
PAIRS = os.path.join(_FRESH, "pairs.json")
BUILD = os.environ.get("MIG_SCRATCH") or os.path.join(tempfile.gettempdir(), "maskedmig_build")
TARS = os.path.join(_ENVS, "fresh_tars")
REPORT = os.path.join(_ENVS, "fresh_build_report.jsonl")
os.environ["UV_PYTHON_PREFERENCE"] = "only-managed"
os.environ.setdefault("UV_CACHE_DIR", os.path.join(_ENVS, "uv_cache"))
os.environ["UV_LINK_MODE"] = "copy"
os.environ.setdefault("CC", "/usr/bin/gcc")
os.environ.setdefault("CXX", "/usr/bin/g++")
IMPORT_NAME = {"scikit-learn": "sklearn", "pillow": "PIL",
               "langchain-core": "langchain_core",
               "huggingface-hub": "huggingface_hub"}


def build_one(job):
    lib, ver, date, py = job
    name = f"fresh_{lib}_{ver}_py{py}".replace("/", "_")
    tar_path = os.path.join(TARS, name + ".tar")
    if os.path.exists(tar_path):
        return name, True, 0, "already built"
    env = os.path.join(BUILD, name)
    t0 = time.time()
    step = "venv"
    try:
        subprocess.run(["rm", "-rf", env], check=True)
        subprocess.run(["uv", "venv", "--python", py, env], check=True,
                       capture_output=True, timeout=600)
        subprocess.run(["uv", "pip", "install", "--python", f"{env}/bin/python",
                        "setuptools", "wheel", "pip"], check=True,
                       capture_output=True, timeout=600)
        step = "install"
        cutoff = (datetime.date.fromisoformat(date)
                  + datetime.timedelta(days=45)).isoformat() + "T00:00:00Z"
        cp = subprocess.run(["uv", "pip", "install", "--python",
                             f"{env}/bin/python", "--exclude-newer", cutoff,
                             "--no-build-isolation", f"{lib}=={ver}"],
                            capture_output=True, timeout=2400, text=True)
        if cp.returncode != 0:
            step = "install-pip"
            cp = subprocess.run([f"{env}/bin/pip", "install", "-q",
                                 f"{lib}=={ver}"],
                                capture_output=True, timeout=2400, text=True)
            if cp.returncode != 0:
                raise RuntimeError(cp.stderr[-400:])
        step = "validate"
        top = IMPORT_NAME.get(lib, lib.replace("-", "_"))
        code = (f"import {top}\n"
                f"import importlib.metadata as md\n"
                f"v = md.version('{lib}')\n"
                f"assert v == '{ver}', v\nprint(v)")
        cp = subprocess.run([f"{env}/bin/python", "-c", code],
                            capture_output=True, timeout=180, text=True)
        if cp.returncode != 0:
            raise RuntimeError("validate: " + (cp.stderr or "")[-300:])
        step = "tar"
        subprocess.run(["tar", "-C", BUILD, "-cf", tar_path, name],
                       check=True, timeout=1200)
        subprocess.run(["rm", "-rf", env], check=True)
        return name, True, round(time.time() - t0), cp.stdout.strip()
    except Exception as e:                                       # noqa: BLE001
        subprocess.run(["rm", "-rf", env], check=False)
        return name, False, round(time.time() - t0), f"[{step}] {e}"[:300]


if __name__ == "__main__":   # 只在直接运行时动手, 避免被 import 时就开始建环境/写文件
    os.makedirs(BUILD, exist_ok=True)
    os.makedirs(TARS, exist_ok=True)
    pairs = json.load(open(PAIRS))
    jobs = []
    for p in pairs:
        jobs.append((p["lib"], p["old_ver"], p["old_date"], p["python"]))
        jobs.append((p["lib"], p["new_ver"], p["new_date"], p["python"]))
    print(f"{len(jobs)} 个环境待建", flush=True)
    ok = 0
    with cf.ThreadPoolExecutor(4) as ex, open(REPORT, "a") as fh:
        for name, good, sec, msg in ex.map(build_one, jobs):
            ok += good
            fh.write(json.dumps({"env": name, "ok": good, "sec": sec,
                                 "msg": msg}) + "\n")
            fh.flush()
            print(f"{'PASS' if good else 'FAIL':4s} {name:40s} {sec:>4}s {msg[:100]}",
                  flush=True)
    print(f"\n完成 {ok}/{len(jobs)}")
