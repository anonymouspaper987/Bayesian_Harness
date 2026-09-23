"""最小 LLM 客户端与单命令代理循环 (gcham 包自成一体, 随基准发布)."""
import json
import re
import urllib.request


def chat(port, model, msgs, max_tokens=1200, temperature=0.0):
    body = json.dumps({
        "model": model, "messages": msgs, "max_tokens": max_tokens,
        "temperature": temperature,
        "chat_template_kwargs": {"enable_thinking": False}}).encode()
    req = urllib.request.Request(
        f"http://localhost:{port}/v1/chat/completions", data=body,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as f:
        out = json.load(f)
    txt = out["choices"][0]["message"]["content"] or ""
    return re.sub(r"<think>.*?</think>", "", txt, flags=re.S).strip()


BASH_RE = re.compile(
    r"```(bash|sh|python|py|mswea_bash_command)?\s*\n(.*?)\n```", re.S)

SHELL_HEADS = {"python", "python3", "pip", "ls", "cat", "grep", "sed", "echo",
               "head", "tail", "find", "cd", "pwd", "which", "env", "export"}


def as_command(lang, body):
    """python 围栏里若是纯 python 代码, 包成 heredoc 交给 bash 执行"""
    first = (body.strip().split() or [""])[0]
    if lang in ("python", "py") and first not in SHELL_HEADS:
        return "python3 - <<'PYCODE_EOF'\n" + body + "\nPYCODE_EOF"
    return body


def parse_marker_json(reply, marker):
    """答案行: MARKER {json}; 支持藏在代码块里"""
    if marker not in (reply or ""):
        return None
    tail = reply.split(marker, 1)[1]
    depth, start = 0, None
    for i, ch in enumerate(tail):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    return json.loads(tail[start:i + 1])
                except Exception:                                # noqa: BLE001
                    start = None
    return None


def agent_loop(env, port, model, system, user, marker, max_steps,
               max_tokens=1200):
    """一回合一命令的代理循环; 以 marker JSON 结束 (marker 行不计费).
    返回 (json_or_None, steps, transcript)"""
    msgs = [{"role": "system", "content": system},
            {"role": "user", "content": user}]
    for step in range(1, max_steps + 1):
        try:
            reply = chat(port, model, msgs, max_tokens=max_tokens)
        except Exception as e:                                   # noqa: BLE001
            msgs.append({"role": "user",
                         "content": f"request failed: {type(e).__name__}"})
            continue
        msgs.append({"role": "assistant", "content": reply})
        got = parse_marker_json(reply, marker)
        if got is not None:
            return got, step, msgs
        m = BASH_RE.search(reply)
        if not m:
            msgs.append({"role": "user", "content":
                         f"Reply with exactly one bash code block, or finish "
                         f"with the {marker} line."})
            continue
        out = env.execute(as_command(m.group(1) or "", m.group(2)))
        msgs.append({"role": "user",
                     "content": f"<output>\n{out['output']}\n</output>"})
    for msg in reversed(msgs):
        if msg["role"] == "assistant":
            got = parse_marker_json(msg["content"], marker)
            if got is not None:
                return got, max_steps, msgs
    return None, max_steps, msgs
