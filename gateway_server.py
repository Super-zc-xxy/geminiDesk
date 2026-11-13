import os
import sys
import json
import uuid
import time
import httpx
from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.responses import StreamingResponse, JSONResponse
import uvicorn

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "settings_config.json")
INTERNAL_GEMINI_PORT = 8085

app = FastAPI(title="Web2API Gemini Dedicated Gateway")

def get_current_model_choice():
    """读取用户在 UI 中选择的目标模型"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("selected_model", "gemini-3.8-flash")
        except Exception:
            pass
    return "gemini-3.8-flash"

def resolve_target_model(client_model: str) -> str:
    # 1. 软件界面选定优先：如果用户在 Web2API Hub 界面中手动选定了具体模型（非 auto），以界面选择为准
    chosen = get_current_model_choice()
    if chosen and chosen != "auto":
        return chosen

    # 2. 如果软件界面设置为 auto，则以客户端（Trae / Cursor 等）当前选定的模型为准
    if client_model and client_model not in ("default", "auto", ""):
        # 如果客户端传来的是旧的 gpt 相关代号，自动重定向到极速 Gemini 代码模型
        if any(kw in client_model.lower() for kw in ["gpt", "o1", "o3", "chatgpt"]):
            return "gemini-3.8-flash"
        return client_model

    return "gemini-3.8-flash"

@app.get("/v1/models")
async def list_models():
    now = int(time.time())
    models_set = {
        "gemini-4-hydrogen",
        "hydrogen",
        "gemini-4-argon",
        "argon",
        "gemini-4-carbon",
        "gemini-3.8-flash",
        "gemini-flash-lite",
        "gemini-3.5-flash-thinking",
        "gemini-3.1-pro",
        "gemini-auto",
        "auto"
    }

    # 从 Gemini (8085) 动态探测
    try:
        async with httpx.AsyncClient(timeout=2.0, trust_env=False) as client:
            resp = await client.get(f"http://127.0.0.1:{INTERNAL_GEMINI_PORT}/v1/models")
            if resp.status_code == 200:
                for item in resp.json().get("data", []):
                    mid = item.get("id")
                    if mid and not any(kw in mid.lower() for kw in ["gpt", "o1", "o3", "chatgpt", "terra", "luna"]):
                        models_set.add(mid)
    except Exception:
        pass

    sorted_models = sorted(list(models_set))
    return {
        "object": "list",
        "data": [{"id": m, "object": "model", "created": now, "owned_by": "web2api-hub"} for m in sorted_models]
    }

async def call_gemini_backend_stream(messages: list, target_model: str):
    backend_url = f"http://127.0.0.1:{INTERNAL_GEMINI_PORT}/v1/chat/completions"
    payload = {
        "model": target_model,
        "messages": messages,
        "stream": True
    }

    async with httpx.AsyncClient(timeout=120.0, trust_env=False) as client:
        try:
            async with client.stream("POST", backend_url, json=payload) as resp:
                if resp.status_code != 200:
                    err_text = await resp.aread()
                    yield f"[Gemini核心 提示 {resp.status_code}]: 请确认后端服务已启动！\n详情: {err_text.decode('utf-8', errors='ignore')}"
                    return

                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        data_json = json.loads(data_str)
                        choices = data_json.get("choices", [])
                        if choices:
                            delta_content = choices[0].get("delta", {}).get("content", "")
                            if delta_content:
                                yield delta_content
                    except Exception:
                        continue
        except httpx.ConnectError:
            yield f"【连接失败】：无法连接到 Gemini 核心 (端口 {INTERNAL_GEMINI_PORT})。请在 Web2API 管理中心中确认对应服务处于运行中！"
        except Exception as e:
            yield f"[转接异常]: {str(e)}"

# ================= 适配 Codex (/v1/responses) =================
@app.post("/v1/responses")
async def handle_responses_api(request: Request):
    body = await request.json()
    client_model = body.get("model", "gpt-4o")
    target_model = resolve_target_model(client_model)
    stream = body.get("stream", True)

    messages = []
    instructions = body.get("instructions")
    if instructions:
        messages.append({"role": "system", "content": instructions})

    input_val = body.get("input") or body.get("messages", [])
    if isinstance(input_val, str):
        messages.append({"role": "user", "content": input_val})
    elif isinstance(input_val, list):
        for item in input_val:
            if isinstance(item, str):
                messages.append({"role": "user", "content": item})
            elif isinstance(item, dict):
                role = item.get("role", "user")
                content = item.get("content", "")
                if isinstance(content, list):
                    text_parts = []
                    for part in content:
                        if isinstance(part, dict):
                            text_parts.append(part.get("text", "") or part.get("input_text", ""))
                        elif isinstance(part, str):
                            text_parts.append(part)
                    content = " ".join(text_parts)
                messages.append({"role": role, "content": content})

    if not messages:
        messages = [{"role": "user", "content": "你好"}]

    guard_instruction = "Answer directly in text or code without invoking Google Drive, Gmail, or Google Workspace extensions."
    if messages and messages[0].get("role") == "system":
        messages[0]["content"] = f"{messages[0].get('content', '')}\n{guard_instruction}"
    else:
        messages.insert(0, {"role": "system", "content": guard_instruction})

    resp_id = f"resp_{uuid.uuid4().hex[:16]}"
    msg_id = f"msg_{uuid.uuid4().hex[:16]}"
    created_time = int(time.time())

    if stream:
        async def response_stream_generator():
            accumulated_text = ""
            try:
                # 1. response.created
                created_evt = {
                    "type": "response.created",
                    "response": {
                        "id": resp_id,
                        "object": "response",
                        "status": "in_progress",
                        "model": target_model,
                        "created": created_time
                    }
                }
                yield f"event: response.created\ndata: {json.dumps(created_evt, ensure_ascii=False)}\n\n"

                # 2. response.output_item.added
                item_added_evt = {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {
                        "id": msg_id,
                        "type": "message",
                        "role": "assistant",
                        "status": "in_progress",
                        "content": []
                    }
                }
                yield f"event: response.output_item.added\ndata: {json.dumps(item_added_evt, ensure_ascii=False)}\n\n"

                # 3. 逐字流式打字
                async for delta in call_gemini_backend_stream(messages, target_model):
                    accumulated_text += delta
                    delta_evt = {
                        "type": "response.output_text.delta",
                        "output_index": 0,
                        "content_index": 0,
                        "delta": delta
                    }
                    yield f"event: response.output_text.delta\ndata: {json.dumps(delta_evt, ensure_ascii=False)}\n\n"

                # 4. output_item.done
                item_done_evt = {
                    "type": "response.output_item.done",
                    "output_index": 0,
                    "item": {
                        "id": msg_id,
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "text", "text": accumulated_text}]
                    }
                }
                yield f"event: response.output_item.done\ndata: {json.dumps(item_done_evt, ensure_ascii=False)}\n\n"

                # 5. response.completed
                completed_evt = {
                    "type": "response.completed",
                    "response": {
                        "id": resp_id,
                        "object": "response",
                        "status": "completed",
                        "model": target_model,
                        "output": [
                            {
                                "id": msg_id,
                                "type": "message",
                                "role": "assistant",
                                "content": [{"type": "text", "text": accumulated_text}]
                            }
                        ]
                    }
                }
                yield f"event: response.completed\ndata: {json.dumps(completed_evt, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
            except Exception as e:
                err_evt = {
                    "type": "response.output_text.delta",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": f"\n[异常]: {str(e)}"
                }
                yield f"event: response.output_text.delta\ndata: {json.dumps(err_evt, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"

        return StreamingResponse(response_stream_generator(), media_type="text/event-stream")
    else:
        full_text = ""
        async for delta in call_gemini_backend_stream(messages, target_model):
            full_text += delta
        return {
            "id": resp_id,
            "object": "response",
            "created": created_time,
            "model": target_model,
            "status": "completed",
            "output": [
                {
                    "id": msg_id,
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": full_text}]
                }
            ]
        }

def extract_text_content(val):
    if isinstance(val, str):
        return val
    if isinstance(val, list):
        parts = []
        for p in val:
            if isinstance(p, dict):
                parts.append(p.get("text", "") or p.get("input_text", "") or "")
            elif isinstance(p, str):
                parts.append(p)
        return " ".join(parts)
    return ""

# ================= 兼容常规客户端 (/v1/chat/completions) =================
@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    client_model = body.get("model", "gemini-4-argon")
    target_model = resolve_target_model(client_model)
    body["model"] = target_model

    raw_messages = body.get("messages", [])
    # 自动净化过滤被 Google Workspace / Drive 错误污染的历史轮次
    messages = []
    for m in raw_messages:
        content_txt = extract_text_content(m.get("content", ""))
        if "I can't use Google Drive" in content_txt or "required Gmail settings are off" in content_txt:
            continue
        messages.append(m)

    if not messages:
        messages = [{"role": "user", "content": "hi"}]

    backend_url = f"http://127.0.0.1:{INTERNAL_GEMINI_PORT}/v1/chat/completions"

    # 针对 Google Gemini 引擎加入静默防护，避免触发 Google Workspace / Gmail 插件提示
    guard_instruction = "Answer directly in text or code without invoking Google Drive, Gmail, or Google Workspace extensions."
    if messages and messages[0].get("role") == "system":
        messages[0]["content"] = f"{extract_text_content(messages[0].get('content', ''))}\n{guard_instruction}"
    else:
        messages.insert(0, {"role": "system", "content": guard_instruction})

    body["messages"] = messages
    headers = {"Content-Type": "application/json"}
    if "authorization" in request.headers:
        headers["Authorization"] = request.headers["authorization"]

    if body.get("stream", False):
        async def stream_generator():
            async with httpx.AsyncClient(timeout=120.0, trust_env=False) as client:
                req = client.build_request("POST", backend_url, json=body, headers=headers)
                r = await client.send(req, stream=True)
                async for chunk in r.aiter_raw():
                    yield chunk

        return StreamingResponse(
            stream_generator(),
            status_code=200,
            headers={
                "Content-Type": "text/event-stream; charset=utf-8",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            }
        )
    else:
        async with httpx.AsyncClient(timeout=120.0, trust_env=False) as client:
            resp = await client.post(backend_url, json=body, headers=headers)
            return Response(
                content=resp.content,
                status_code=resp.status_code,
                media_type=resp.headers.get("content-type", "application/json")
            )

if __name__ == "__main__":
    port = 8090
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except Exception:
            pass
    print(f"[Gemini Universal Gateway] Listening on http://0.0.0.0:{port} -> Internal Engine :8085")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
