import os
import sys
import json
import uuid
import time
import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
import uvicorn

app = FastAPI(title="Codex Bridge to Gemini 4 Argon")

GEMINI_BACKEND_URL = "http://127.0.0.1:8081/v1/chat/completions"

@app.get("/v1/models")
async def list_models():
    models = [
        "gpt-4o", "4o", "gpt-4o-mini", "o1", "o3-mini",
        "gemini-4-argon", "argon", "gemini-3.8-flash"
    ]
    return {
        "object": "list",
        "data": [{"id": m, "object": "model", "created": int(time.time()), "owned_by": "gemini-bridge"} for m in models]
    }

async def call_gemini_stream(messages: list, model: str = "gemini-4-argon"):
    # 强制映射到满血 4 Argon
    target_model = "gemini-4-argon"

    payload = {
        "model": target_model,
        "messages": messages,
        "stream": True
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            async with client.stream(
                "POST",
                GEMINI_BACKEND_URL,
                json=payload
            ) as resp:
                if resp.status_code != 200:
                    err_text = await resp.aread()
                    yield f"[Gemini服务提示 {resp.status_code}]: 请确认 Web2API 管理中心的 Gemini 服务已点击启动！\n详情: {err_text.decode('utf-8', errors='ignore')}"
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
            yield "【连接失败】：无法连接到本地 8081 端口。请在 Web2API Hub 软件中，确认【Google Gemini】服务卡片处于【🟢 正在运行中】！"
        except Exception as e:
            yield f"[转接异常]: {str(e)}"

# ================= 适配 Codex / Responses API (/v1/responses) =================
@app.post("/v1/responses")
async def handle_responses_api(request: Request):
    body = await request.json()
    model = body.get("model", "gpt-4o")
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
                        "model": model,
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
                async for delta in call_gemini_stream(messages, model):
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
                        "model": model,
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
        async for delta in call_gemini_stream(messages, model):
            full_text += delta
        return {
            "id": resp_id,
            "object": "response",
            "created": created_time,
            "model": model,
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

# ================= 兼容传统 OpenAI 聊天补全 (/v1/chat/completions) =================
@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    model = body.get("model", "gemini-4-argon")
    messages = body.get("messages", [])
    stream = body.get("stream", False)

    created_time = int(time.time())
    req_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

    if stream:
        async def stream_generator():
            try:
                async for delta in call_gemini_stream(messages, model):
                    chunk = {
                        "id": req_id,
                        "object": "chat.completion.chunk",
                        "created": created_time,
                        "model": model,
                        "choices": [{"index": 0, "delta": {"content": delta}, "finish_reason": None}]
                    }
                    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

                end_chunk = {
                    "id": req_id,
                    "object": "chat.completion.chunk",
                    "created": created_time,
                    "model": model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]
                }
                yield f"data: {json.dumps(end_chunk, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
            except Exception as e:
                err_chunk = {
                    "id": req_id,
                    "object": "chat.completion.chunk",
                    "created": created_time,
                    "model": model,
                    "choices": [{"index": 0, "delta": {"content": f"\n[异常]: {str(e)}"}, "finish_reason": "stop"}]
                }
                yield f"data: {json.dumps(err_chunk, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"

        return StreamingResponse(stream_generator(), media_type="text/event-stream")
    else:
        full_text = ""
        async for delta in call_gemini_stream(messages, model):
            full_text += delta
        return {
            "id": req_id,
            "object": "chat.completion",
            "created": created_time,
            "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": full_text}, "finish_reason": "stop"}]
        }

if __name__ == "__main__":
    port = 8082
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except Exception:
            pass
    print(f"[Codex Bridge Server] Listening on http://0.0.0.0:{port} -> Forwarding to Gemini 8081")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
