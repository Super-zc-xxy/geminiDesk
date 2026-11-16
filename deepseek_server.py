import os
import sys
import json
import time
import uuid
import base64
import struct
import asyncio
from typing import Optional, Dict, Any, AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
import uvicorn
from curl_cffi.requests import AsyncSession

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(BASE_DIR, "deepseek_token.txt")
WASM_FILE = os.path.join(BASE_DIR, "sha3_wasm_bg.wasm")
ENV_FILE = os.path.join(BASE_DIR, ".env")

app = FastAPI(title="Web2API DeepSeek Dedicated Engine")

# ==================== WASM PoW Solver Core ====================
_wasm_initialized = False
_store = None
_memory = None
_malloc = None
_add_stack = None
_solve_func = None
_hash_func = None

def init_wasm():
    global _wasm_initialized, _store, _memory, _malloc, _add_stack, _solve_func, _hash_func
    if _wasm_initialized:
        return
    if not os.path.exists(WASM_FILE):
        raise FileNotFoundError(f"Missing WASM file at: {WASM_FILE}")
    
    import wasmtime
    engine = wasmtime.Engine()
    _store = wasmtime.Store(engine)
    module = wasmtime.Module.from_file(engine, WASM_FILE)
    instance = wasmtime.Instance(_store, module, [])

    exports = instance.exports(_store)
    _memory = exports["memory"]
    _malloc = exports["__wbindgen_export_0"]
    _add_stack = exports["__wbindgen_add_to_stack_pointer"]
    _solve_func = exports["wasm_solve"]
    _hash_func = exports["wasm_deepseek_hash_v1"]
    _wasm_initialized = True

def hash_v1(input_str: str) -> str:
    """Compute DeepSeekHashV1 for a given string."""
    init_wasm()
    ret_addr = _add_stack(_store, -16)
    b = input_str.encode("utf-8")
    p = _malloc(_store, len(b), 1)
    _memory.write(_store, b, p)
    try:
        _hash_func(_store, ret_addr, p, len(b))
        raw = _memory.read(_store, ret_addr, ret_addr + 16)
        out_ptr, out_len = struct.unpack("<IIII", raw)[:2]
        return bytes(_memory.read(_store, out_ptr, out_ptr + out_len)).decode("utf-8")
    finally:
        _add_stack(_store, 16)

def solve_pow(challenge: str, prefix: str, difficulty: float) -> Optional[int]:
    """
    Solve DeepSeek PoW challenge using the wasm binary.
    prefix is format: '{salt}_{expire_at}_'
    difficulty is the search ceiling / difficulty float.
    """
    init_wasm()
    c_bytes = challenge.encode("utf-8")
    p_bytes = prefix.encode("utf-8")

    c_ptr = _malloc(_store, len(c_bytes), 1)
    _memory.write(_store, c_bytes, c_ptr)

    p_ptr = _malloc(_store, len(p_bytes), 1)
    _memory.write(_store, p_bytes, p_ptr)

    ret_addr = _add_stack(_store, -16)
    try:
        _solve_func(_store, ret_addr, c_ptr, len(c_bytes), p_ptr, len(p_bytes), float(difficulty))
        raw = _memory.read(_store, ret_addr, ret_addr + 16)
        status = struct.unpack("<I", raw[:4])[0]
        val = struct.unpack("<d", raw[8:16])[0]
        if status == 0:
            return None
        return int(val)
    finally:
        _add_stack(_store, 16)

# ==================== Configuration & Utilities ====================
def get_token() -> Optional[str]:
    """Read and validate user token from deepseek_token.txt."""
    if not os.path.exists(TOKEN_FILE):
        return None
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "请在此处" in line or "<" in line:
                    continue
                # Found valid-looking token
                return line
    except Exception:
        pass
    return None

def get_proxy() -> Optional[str]:
    """Check for proxy configuration in .env or environment."""
    if os.path.exists(ENV_FILE):
        try:
            with open(ENV_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("PROXY_URL=") or line.startswith("proxy_url="):
                        val = line.split("=", 1)[1].strip()
                        if val:
                            return val
        except Exception:
            pass
    return os.environ.get("PROXY_URL") or os.environ.get("https_proxy") or os.environ.get("http_proxy")

DEEPSEEK_MODELS = [
    "deepseek-v4.1-flash",
    "deepseek-v4.1-pro",
    "deepseek-v4.1-thinking",
    "deepseek-chat",
    "deepseek-reasoner",
]

# ==================== OpenAI Compatible Endpoints ====================
@app.get("/v1/models")
async def list_models():
    now = int(time.time())
    data = [
        {
            "id": m,
            "object": "model",
            "created": now,
            "owned_by": "deepseek"
        }
        for m in DEEPSEEK_MODELS
    ]
    return {"object": "list", "data": data}

def make_graceful_missing_token_response(stream: bool, model: str):
    msg = (
        "【DeepSeek 提示】：未检测到有效的 DeepSeek UserToken。\n"
        "请在 deepseek_token.txt 中填入从 chat.deepseek.com 网页端获取的 userToken 后重试！\n"
        "(获取方法：登录 chat.deepseek.com -> F12 打开开发者工具 -> Application -> Local Storage -> userToken)"
    )
    cmpl_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    now = int(time.time())

    if not stream:
        return JSONResponse(
            content={
                "id": cmpl_id,
                "object": "chat.completion",
                "created": now,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": msg
                        },
                        "finish_reason": "stop"
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": len(msg),
                    "total_tokens": 10 + len(msg)
                }
            }
        )

    async def fake_stream():
        chunk = {
            "id": cmpl_id,
            "object": "chat.completion.chunk",
            "created": now,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "content": msg
                    },
                    "finish_reason": None
                }
            ]
        }
        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        done_chunk = {
            "id": cmpl_id,
            "object": "chat.completion.chunk",
            "created": now,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": "stop"
                }
            ]
        }
        yield f"data: {json.dumps(done_chunk, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(fake_stream(), media_type="text/event-stream")

async def execute_deepseek_call(token: str, prompt: str, thinking_enabled: bool, model: str) -> AsyncGenerator[Dict[str, Any], None]:
    """Execute authenticated DeepSeek Web API call with WASM PoW and yield parsed deltas."""
    proxy = get_proxy()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Origin": "https://chat.deepseek.com",
        "Referer": "https://chat.deepseek.com/",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    async with AsyncSession(impersonate="chrome131", proxy=proxy, timeout=120.0) as session:
        # 1. Create chat session
        session_id = None
        try:
            r = await session.post("https://chat.deepseek.com/api/v0/chat_session/create", json={}, headers=headers)
            if r.status_code == 200:
                res_json = r.json()
                if res_json.get("code") != 0:
                    err_msg = res_json.get("msg", "INVALID_TOKEN")
                    yield {
                        "type": "error",
                        "content": f"【DeepSeek 提示】：用户凭据无效或已过期 ({err_msg})。\n请在【系统凭据与高级配置 -> DeepSeek Token 凭据】或 deepseek_token.txt 中更新最新 userToken！"
                    }
                    return
                session_id = res_json.get("data", {}).get("biz_data", {}).get("chat_session", {}).get("id")
        except Exception as e:
            pass

        if not session_id:
            session_id = uuid.uuid4().hex

        # 2. Request PoW Challenge
        pow_header = None
        try:
            r_pow = await session.post(
                "https://chat.deepseek.com/api/v0/chat/create_pow_challenge",
                json={"target_path": "/api/v0/chat/completion"},
                headers=headers
            )
            if r_pow.status_code == 200:
                p_data = r_pow.json().get("data", {}).get("biz_data", {})
                challenge_obj = p_data.get("challenge", {})
                algo = challenge_obj.get("algorithm")
                challenge_str = challenge_obj.get("challenge")
                salt = challenge_obj.get("salt")
                difficulty = challenge_obj.get("difficulty", 140000)
                expire_at = challenge_obj.get("expire_at")
                signature = challenge_obj.get("signature")

                if algo == "DeepSeekHashV1" and challenge_str and salt and expire_at:
                    prefix = f"{salt}_{expire_at}_"
                    answer = solve_pow(challenge_str, prefix, difficulty)
                    if answer is not None:
                        pow_resp_dict = {
                            "algorithm": algo,
                            "challenge": challenge_str,
                            "salt": salt,
                            "answer": answer,
                            "signature": signature,
                            "target_path": "/api/v0/chat/completion"
                        }
                        pow_header = base64.b64encode(json.dumps(pow_resp_dict).encode("utf-8")).decode("utf-8")
        except Exception:
            pass

        call_headers = dict(headers)
        if pow_header:
            call_headers["X-DS-PoW-Response"] = pow_header

        # 3. Send completion request
        payload = {
            "chat_session_id": session_id,
            "parent_message_id": None,
            "prompt": prompt,
            "ref_file_ids": [],
            "thinking_enabled": thinking_enabled,
            "search_enabled": False
        }

        resp = await session.post(
            "https://chat.deepseek.com/api/v0/chat/completion",
            json=payload,
            headers=call_headers,
            stream=True
        )

        if resp.status_code != 200:
            err_text = resp.text
            yield {"type": "error", "content": f"[DeepSeek 服务返回异常 {resp.status_code}]: {err_text}"}
            return

        # Track fragment types to distinguish thinking from final text
        # fragment index -> type (e.g. 'THINK' or 'TEXT')
        fragment_types = {}
        last_seen_type = "TEXT"

        async for line in resp.aiter_lines():
            line_str = line.decode("utf-8", errors="ignore").strip() if isinstance(line, bytes) else line.strip()
            if not line_str:
                continue
            if line_str.startswith("{"):
                try:
                    err_json = json.loads(line_str)
                    if err_json.get("code") != 0:
                        err_msg = err_json.get("msg", "UNKNOWN_ERROR")
                        yield {
                            "type": "error",
                            "content": f"【DeepSeek 服务提示】：{err_msg}\n请确认当前账号状态或刷新网页端 Token。"
                        }
                        return
                except Exception:
                    pass
            if not line_str.startswith("data:"):
                continue

            data_content = line_str[5:].strip()
            if data_content == "[DONE]":
                break

            try:
                data_json = json.loads(data_content)
            except Exception:
                continue

            # Check delta operation
            # e.g. {"v": "...", "p": "response/fragments/0/content", "o": "APPEND"}
            val = data_json.get("v")
            path = data_json.get("p", "")
            op = data_json.get("o", "")

            # If path indicates fragment type definition: e.g. response/fragments/0/type
            if "fragments/" in path and "/type" in path and isinstance(val, str):
                parts = path.split("/")
                try:
                    idx = parts[parts.index("fragments") + 1]
                    fragment_types[idx] = val.upper()
                except Exception:
                    pass
                continue

            if val is not None and isinstance(val, str) and val:
                # Determine whether this is thinking or text
                is_thinking = False
                if "fragments/" in path:
                    parts = path.split("/")
                    try:
                        idx = parts[parts.index("fragments") + 1]
                        frag_type = fragment_types.get(idx, "")
                        if frag_type == "THINK":
                            is_thinking = True
                        elif frag_type == "TEXT":
                            is_thinking = False
                        else:
                            # Default heuristic: if thinking_enabled and not yet final text
                            is_thinking = thinking_enabled and ("think" in path.lower() or idx == "0")
                    except Exception:
                        is_thinking = thinking_enabled and "think" in path.lower()
                elif "think" in path.lower():
                    is_thinking = True

                if is_thinking:
                    yield {"type": "reasoning", "content": val}
                else:
                    yield {"type": "content", "content": val}

@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    model = body.get("model", "deepseek-v4.1-flash")
    stream = body.get("stream", False)
    messages = body.get("messages", [])

    token = get_token()
    if not token:
        return make_graceful_missing_token_response(stream=stream, model=model)

    # Format messages into single prompt
    prompt_parts = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, list):
            content = " ".join([p.get("text", "") for p in content if isinstance(p, dict)])
        if role == "system":
            prompt_parts.append(f"[System]: {content}")
        elif role == "user":
            prompt_parts.append(f"{content}")
        elif role == "assistant":
            prompt_parts.append(f"[Assistant]: {content}")

    prompt = "\n\n".join(prompt_parts) if prompt_parts else "Hello"

    # Determine thinking mode
    model_lower = model.lower()
    thinking_enabled = any(k in model_lower for k in ["thinking", "reasoner", "r1"])

    cmpl_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    now = int(time.time())

    if stream:
        async def event_generator():
            async for item in execute_deepseek_call(token, prompt, thinking_enabled, model):
                itype = item.get("type")
                icontent = item.get("content", "")
                delta = {}
                if itype == "reasoning":
                    delta = {"reasoning_content": icontent}
                elif itype in ("content", "error"):
                    delta = {"content": icontent}

                chunk = {
                    "id": cmpl_id,
                    "object": "chat.completion.chunk",
                    "created": now,
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": delta,
                            "finish_reason": None
                        }
                    ]
                }
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

            # Final stop chunk
            final_chunk = {
                "id": cmpl_id,
                "object": "chat.completion.chunk",
                "created": now,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": "stop"
                    }
                ]
            }
            yield f"data: {json.dumps(final_chunk, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    else:
        full_content = []
        full_reasoning = []
        async for item in execute_deepseek_call(token, prompt, thinking_enabled, model):
            itype = item.get("type")
            icontent = item.get("content", "")
            if itype == "reasoning":
                full_reasoning.append(icontent)
            else:
                full_content.append(icontent)

        content_str = "".join(full_content)
        reasoning_str = "".join(full_reasoning) if full_reasoning else None

        msg_obj = {"role": "assistant", "content": content_str}
        if reasoning_str:
            msg_obj["reasoning_content"] = reasoning_str

        return JSONResponse(
            content={
                "id": cmpl_id,
                "object": "chat.completion",
                "created": now,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": msg_obj,
                        "finish_reason": "stop"
                    }
                ],
                "usage": {
                    "prompt_tokens": len(prompt),
                    "completion_tokens": len(content_str),
                    "total_tokens": len(prompt) + len(content_str)
                }
            }
        )

# ==================== Server Entrypoint ====================
if __name__ == "__main__":
    port = 8087
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            pass
    print(f"[DeepSeek Server] Starting on http://127.0.0.1:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port)
