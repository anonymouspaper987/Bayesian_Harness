"""模型后端切换: 名字以 gemini 开头走 Vertex API, 否则走本地 vLLM (gcham/llm.chat).

用法: install(model) 之后, gcham.llm.agent_loop 内部的 chat 会被替换.
"""
import os
import threading
import time

_client = None
_lock = threading.Lock()
_last = [0.0]
REQUEST_GAP = float(os.environ.get("GEMINI_GAP", "0.3"))
QUOTA_RETRIES = int(os.environ.get("GEMINI_QUOTA_RETRIES", "24"))   # 5+10+...+120*19 ≈ 40 分钟
USAGE = {"calls": 0, "in": 0, "out": 0, "think": 0, "q429": 0}   # 累计 token 用量 (只对 Vertex 通道)


def _vertex_client():
    global _client
    if _client is None:
        # Vertex AI credentials come from the environment; see README.
        # GOOGLE_APPLICATION_CREDENTIALS -> service-account key file
        # GOOGLE_CLOUD_PROJECT           -> your GCP project id
        for var in ("GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT"):
            if not os.environ.get(var):
                raise RuntimeError(f"{var} is not set; needed for the Gemini lane")
        os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")
        os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")
        import logging
        logging.getLogger('google_genai').setLevel(logging.ERROR)
        logging.getLogger('google.genai').setLevel(logging.ERROR)
        from google import genai
        from google.genai import types
        _client = genai.Client(http_options=types.HttpOptions(timeout=240_000))
    return _client


def gemini_chat(model, msgs, max_tokens=1200, temperature=0.0, max_retries=6):
    from google.genai import types
    client = _vertex_client()
    system = "\n\n".join(m["content"] for m in msgs if m["role"] == "system") or None
    contents = [types.Content(role="user" if m["role"] == "user" else "model",
                              parts=[types.Part(text=m["content"])])
                for m in msgs if m["role"] != "system"]
    # 思考 token 按输出计费, 是花费大头: 2.5 系列关掉; 3.x 系列关不掉, 用最低档 (可用环境变量 GEMINI_THINKING 改)
    if model.startswith("gemini-3"):
        tc = types.ThinkingConfig(thinking_level=os.environ.get("GEMINI_THINKING", "low"))
    else:
        tc = types.ThinkingConfig(thinking_budget=0)
    config = types.GenerateContentConfig(system_instruction=system, temperature=temperature,
                                         max_output_tokens=max_tokens, thinking_config=tc)
    delay = 5
    for attempt in range(max(max_retries, QUOTA_RETRIES)):
        with _lock:
            gap = REQUEST_GAP - (time.time() - _last[0])
            if gap > 0:
                time.sleep(gap)
            _last[0] = time.time()
        try:
            r = client.models.generate_content(model=model, contents=contents, config=config)
            u = getattr(r, "usage_metadata", None)
            if u is not None:
                with _lock:
                    USAGE["calls"] += 1
                    USAGE["in"] += u.prompt_token_count or 0
                    USAGE["out"] += u.candidates_token_count or 0
                    USAGE["think"] += getattr(u, "thoughts_token_count", 0) or 0
            return (r.text or "").strip()
        except Exception as e:                                       # noqa: BLE001
            msg = str(e)
            retriable = any(k in msg for k in ["429", "RESOURCE_EXHAUSTED", "timed out",
                                                "timeout", "503", "UNAVAILABLE", "504",
                                                "DEADLINE", "500", "INTERNAL"])
            quota = ("429" in msg) or ("RESOURCE_EXHAUSTED" in msg)
            # 配额 429 单独处理: 最多耐心等 QUOTA_PATIENCE 秒 (默认 40 分钟), 不然一次 429 风暴就把整月记成 0 分
            if quota and attempt < QUOTA_RETRIES - 1:
                with _lock:
                    USAGE["q429"] += 1
                time.sleep(delay)
                delay = min(delay * 2, 120)
                continue
            if retriable and attempt < max_retries - 1:
                time.sleep(delay)
                delay = min(delay * 2, 60)
                continue
            raise


def install(model):
    """按模型名把 gcham.llm.chat 换成对应后端 (agent_loop 通过模块全局名查 chat)."""
    if not model.startswith("gemini"):
        return "vllm"
    import llm

    def chat(port, model_, msgs, max_tokens=1200, temperature=0.0):
        return gemini_chat(model_, msgs, max_tokens=max_tokens, temperature=temperature)
    llm.chat = chat
    return "vertex"
