"""合成世界的执行环境: 按月切版本 (PYTHONPATH), 私有工作目录, 计费 bash."""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
# World data. Override with ACMESDK_DATA to keep the (large) worlds outside the repo.
ROOT = os.environ.get("ACMESDK_DATA") or os.path.join(REPO, "data", "acmesdk")
# Per-run private working dir. Override with ACMESDK_SCRATCH (use fast local disk).
SCRATCH = os.environ.get("ACMESDK_SCRATCH") or os.path.join(tempfile.gettempdir(), "acmesdk_work")


def seed_dir(seed):
    """变体世界 (同一种子的 API 规格, 不同漂移结构) 用 ACME_WORLD_SUFFIX 区分目录."""
    return os.path.join(ROOT, f"seed{seed}{os.environ.get('ACME_WORLD_SUFFIX', '')}")


def version_dir(seed, m):
    return os.path.join(seed_dir(seed), "versions", f"M{m:02d}")


class SynthEnv:
    """接口与 GchamEnv 对齐: execute(cmd) -> {output, returncode}; run_py(code) -> (ok, err)."""

    def __init__(self, seed, month, call_budget=0, max_output_chars=2500,
                 timeout=60, python=None):
        self.seed, self.month = seed, month
        self.call_budget = call_budget
        self.max_output_chars = max_output_chars
        self.timeout = timeout
        self.n_calls = 0
        self.refused = 0
        self.vdir = version_dir(seed, month)
        assert os.path.isdir(self.vdir), self.vdir
        os.makedirs(SCRATCH, exist_ok=True)
        self.workdir = tempfile.mkdtemp(prefix="acme_ep_", dir=SCRATCH)
        self.python = python or sys.executable

    def _env(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = self.vdir
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        # agent 的 bash 里 python/python3 必须是编译字节码的同版解释器
        env["PATH"] = os.path.dirname(self.python) + ":" + env.get("PATH", "")
        return env

    def execute(self, command):
        if self.call_budget and self.n_calls >= self.call_budget:
            self.refused += 1
            return {"output": f"TOOL BUDGET EXHAUSTED after {self.n_calls} calls. "
                              "No further command will run; give your final answer now.",
                    "returncode": 1}
        self.n_calls += 1
        try:
            p = subprocess.run(["bash", "-lc", command], cwd=self.workdir, env=self._env(),
                               capture_output=True, text=True, errors="replace", timeout=self.timeout)
            out = (p.stdout or "") + (p.stderr or "")
            rc = p.returncode
        except subprocess.TimeoutExpired:
            out, rc = f"TIMEOUT after {self.timeout}s", 124
        if len(out) > self.max_output_chars:
            out = out[:self.max_output_chars // 2] + "\n...[truncated]...\n" + out[-self.max_output_chars // 2:]
        return {"output": out, "returncode": rc}

    def run_py(self, code, timeout=45):
        """不计费的机械执行 (探查/判分用)."""
        fd, path = tempfile.mkstemp(suffix=".py", dir=self.workdir)
        with os.fdopen(fd, "w") as f:
            f.write(code)
        try:
            p = subprocess.run([self.python, path], cwd=self.workdir, env=self._env(),
                               capture_output=True, text=True, errors="replace", timeout=timeout)
            return p.returncode == 0, (p.stdout or "") + (p.stderr or "")
        except subprocess.TimeoutExpired:
            return False, "TIMEOUT"

    def cleanup(self):
        shutil.rmtree(self.workdir, ignore_errors=True)
