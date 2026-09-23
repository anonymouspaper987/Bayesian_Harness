"""masked_loop 的 API 版入口: --model 以 gemini 开头时把 llm.chat 换成 Vertex 后端 (synth/llm_backend), 其余逻辑不变.

用法与 masked_loop.py 完全相同, 例如
  python masked_loop_api.py --model gemini-2.5-flash-lite --conditions none --n-tasks 2 --out-prefix /tmp/demo
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "acmesdk"))
import llm_backend                                                # noqa: E402
import world                                                      # noqa: E402
import masked_loop                                                # noqa: E402

# 解包目录可用环境变量 MIG_SCRATCH 改 (默认在系统临时目录).
world.UNPACK = os.environ.get("GCHAM_UNPACK", world.UNPACK)


def _model():
    for i, x in enumerate(sys.argv):
        if x == "--model" and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if x.startswith("--model="):
            return x.split("=", 1)[1]
    return "Qwen/Qwen3.5-27B"


if __name__ == "__main__":
    m = _model()
    print("模型后端:", llm_backend.install(m), m, flush=True)
    try:
        masked_loop.main()
    finally:
        if llm_backend.USAGE["calls"]:
            u = llm_backend.USAGE
            print(f"API 用量: {u['calls']} 次调用, 输入 {u['in']} / 输出 {u['out']} / "
                  f"思考 {u['think']} tokens, 429 重试 {u['q429']}", flush=True)
