"""GitChameleon 世界: 环境 tar 装载 + 计费执行器 + 时间线.

世界的"当前版本"由数据集自身的版本轨迹定义: 库 L 在时刻 t 的版本 =
L 在 t 之前(含)最近一道题所钉的版本。漂移事件 = 该指针前进。
探查/修复在库的当前版本环境里执行, 考试在题目钉死版本的环境里执行,
两者都来自同一批已验证的环境 tar。
"""
import collections
import json
import os
import shutil
import subprocess
import tarfile
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
# Third-party GitChameleon release; see README (not redistributed here).
_GC = os.environ.get("GITCHAMELEON_DIR") or os.path.join(REPO, "data", "gitchameleon")
DS = os.path.join(_GC, "dataset", "final_fix_dataset.jsonl")
# Built pip environments, produced by build_fresh_envs.py. Tens of GB, keep off the repo.
ENVS = os.environ.get("MIG_ENVS") or os.path.join(REPO, "data", "mig_envs")
TARS = os.path.join(ENVS, "tars")
MANIFEST = os.path.join(ENVS, "env_manifest.json")
UNPACK = os.environ.get("MIG_SCRATCH") or os.path.join(tempfile.gettempdir(), "maskedmig_envs")

# 环境内逐题剔除 (参考解在已建环境中不过, 构建报告 2026-09-03)
EXCLUDED = {"283", "285", "286", "312"}


def load_world():
    rows = [json.loads(l) for l in open(DS)]
    env_of = {str(k): v for k, v in json.load(open(MANIFEST)).items()}
    have = {f[:-4] for f in os.listdir(TARS) if f.endswith(".tar")}
    probs = [r for r in rows
             if env_of.get(str(r["example_id"])) in have
             and str(r["example_id"]) not in EXCLUDED]
    for r in probs:
        r["env"] = env_of[str(r["example_id"])]
        y, m = r["release_date"].split("-")[:2]
        r["quarter"] = f"{y}Q{(int(m) - 1) // 3 + 1}"
    return probs


def quarters(probs, start="2021Q1"):
    return [q for q in sorted({r["quarter"] for r in probs}) if q >= start]


def current_env(probs, lib, q):
    """库 lib 在季度 q 的当前环境 = 该库 q 之前(含)最近一题的环境"""
    past = [r for r in probs if r["library"] == lib and r["quarter"] <= q]
    if not past:
        return None
    return max(past, key=lambda r: (r["quarter"], r["release_date"]))["env"]


class GchamEnv:
    """解包一个环境 tar 到固定路径, 提供计费 bash 执行"""

    def __init__(self, env_name, call_budget=0, max_output_chars=2500,
                 timeout=60, tars_dir=None):
        self.tars_dir = tars_dir or TARS
        self.name = env_name
        self.call_budget = call_budget
        self.max_output_chars = max_output_chars
        self.timeout = timeout
        self.n_calls = 0
        self.refused = 0
        self.env_dir = os.path.join(UNPACK, env_name)
        os.makedirs(UNPACK, exist_ok=True)
        if not os.path.isdir(self.env_dir):
            # 原子解包: 先解到私有目录再 rename, 并发下第一个赢, 其余复用
            tmp = tempfile.mkdtemp(prefix=".unpack_", dir=UNPACK)
            with tarfile.open(os.path.join(self.tars_dir, env_name + ".tar")) as tf:
                tf.extractall(tmp)
            src = os.path.join(tmp, env_name)
            try:
                os.rename(src, self.env_dir)
            except OSError:
                pass                       # 别的线程先完成了
            shutil.rmtree(tmp, ignore_errors=True)
        # 每个 episode 独立工作目录 (私有 mkdtemp, 防共享 /tmp 杂散 .py)
        self.workdir = tempfile.mkdtemp(prefix="gcham_ep_", dir=UNPACK)
        self.python = os.path.join(self.env_dir, "bin", "python")

    def execute(self, command):
        if self.call_budget and self.n_calls >= self.call_budget:
            self.refused += 1
            return {"output": f"TOOL BUDGET EXHAUSTED after {self.n_calls} "
                              f"calls. No further command will run; give "
                              f"your final answer now.", "returncode": 1}
        self.n_calls += 1
        env = dict(os.environ)
        env["PATH"] = os.path.join(self.env_dir, "bin") + ":" + env["PATH"]
        env["VIRTUAL_ENV"] = self.env_dir
        env["PYTHONPATH"] = self.workdir
        try:
            cp = subprocess.run(["bash", "-c", command], cwd=self.workdir,
                                capture_output=True, text=True,
                                timeout=self.timeout, env=env)
            out = (cp.stdout or "") + (cp.stderr or "")
            rc = cp.returncode
        except subprocess.TimeoutExpired:
            out, rc = f"command timed out after {self.timeout}s", 124
        cap = self.max_output_chars
        if len(out) > cap:
            out = out[:cap // 2] + "\n...[truncated]...\n" + out[-cap // 2:]
        return {"output": out, "returncode": rc}

    def run_py(self, code, timeout=180):
        """不计费运行一段 python (判分器/探查器专用, 方法不可直接调用)"""
        with tempfile.NamedTemporaryFile("w", suffix=".py", dir=self.workdir,
                                         delete=False) as f:
            f.write(code)
            tmp = f.name
        penv = dict(os.environ)
        penv["PYTHONPATH"] = self.workdir
        try:
            cp = subprocess.run([self.python, tmp], capture_output=True,
                                text=True, timeout=timeout, cwd=self.workdir,
                                env=penv)
            return cp.returncode == 0, (cp.stderr or cp.stdout)[-400:]
        except subprocess.TimeoutExpired:
            return False, "timeout"
        finally:
            os.unlink(tmp)

    def cleanup(self, drop_env=False):
        shutil.rmtree(self.workdir, ignore_errors=True)
        if drop_env:
            shutil.rmtree(self.env_dir, ignore_errors=True)


if __name__ == "__main__":
    probs = load_world()
    qs = quarters(probs)
    warm = [r for r in probs if r["quarter"] < "2021Q1"]
    by_q = collections.Counter(r["quarter"] for r in probs
                               if r["quarter"] >= "2021Q1")
    print(f"可考 {len(probs)} 题; 热身(2021前) {len(warm)} 题; "
          f"考试季度 {len(by_q)} 个: {dict(sorted(by_q.items()))}")
    libs = sorted({r["library"] for r in probs})
    moved = sum(1 for lib in libs
                if current_env(probs, lib, qs[0]) != current_env(probs, lib, qs[-1]))
    print(f"{len(libs)} 个库, 窗口内版本指针移动过的 {moved} 个")
