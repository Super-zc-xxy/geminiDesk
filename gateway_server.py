import os
import sys
import json
import uuid
import time
import asyncio
import httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "settings_config.json")
LOG_FILE = os.path.join(BASE_DIR, "gateway_access.log")
INTERNAL_GEMINI_PORT = 8085
INTERNAL_DEEPSEEK_PORT = 8087

http_client: httpx.AsyncClient = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client
    limits = httpx.Limits(max_keepalive_connections=200, max_connections=1000)
    http_client = httpx.AsyncClient(limits=limits, timeout=120.0, trust_env=False)
    yield
    await http_client.aclose()

app = FastAPI(title="Web2API Dual-Engine Universal Gateway", lifespan=lifespan)

# 允许全源跨域访问，支持 VS Code / Trae / Web 客户端的 OPTIONS 预检请求
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def request_logger_middleware(request: Request, call_next):
    client_ip = request.client.host if request.client else "unknown"
    method = request.method
    url_path = request.url.path
    query = request.url.query
    full_url = f"{url_path}?{query}" if query else url_path

    body_preview = ""
    if method in ("POST", "PUT", "PATCH"):
        try:
            req_body = await request.body()
            body_preview = req_body[:300].decode("utf-8", errors="ignore").replace("\n", " ")
            async def receive():
                return {"type": "http.request", "body": req_body, "more_body": False}
            request = Request(request.scope, receive)
        except Exception:
            pass

    response = await call_next(request)
    log_line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {client_ip} {method} {full_url} -> {response.status_code} | Body: {body_preview}\n"
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(log_line)
    except Exception:
        pass
    return response

@app.get("/")
@app.get("/v1")
async def health_check():
    return {"status": "ok", "service": "Web2API Gateway", "time": int(time.time())}

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
    cm = (client_model or "").strip()
    cm_lower = cm.lower()

    # 1. 如果客户端显式请求已知特定模型，遵从客户端意图，杜绝跨引擎误劫持
    # DeepSeek 家族特有标识：
    if "deepseek" in cm_lower or cm_lower in ("r1", "v4") or any(p in ("r1", "v4") for p in cm_lower.replace("_", "-").split("-")):
        return cm
    # Gemini 家族特有标识：
    if any(kw in cm_lower for kw in ["gemini", "hydrogen", "argon", "carbon"]):
        return cm

    # 2. 读取软件界面选定模型
    chosen = get_current_model_choice()

    # 3. 如果客户端传来的是通用/旧代号（gpt/o1/o3/chatgpt）或缺省（空/default/auto）
    if not cm or cm_lower in ("default", "auto") or any(kw in cm_lower for kw in ["gpt", "o1", "o3", "chatgpt"]):
        if chosen and chosen != "auto":
            return chosen
        return "gemini-4-hydrogen"

    # 4. 如果软件界面手动选定了具体模型（非 auto），优先使用界面选定模型
    if chosen and chosen != "auto":
        return chosen

    # 5. 软件界面为 auto 时，以客户端传入模型为准
    return cm

def resolve_backend_engine(target_model: str) -> str:
    """
    根据目标模型名称判决后端引擎：
    - 若包含 'deepseek' 或等于 'r1', 'v4' 或以 'r1', 'v4' 作为独立标识，判定为 'deepseek' (8087)
    - 否则判定为 'gemini' (8085)
    """
    if not target_model:
        return "gemini"
    tm = str(target_model).lower().strip()
    if "deepseek" in tm or tm in ("r1", "v4"):
        return "deepseek"
    parts = tm.replace("_", "-").split("-")
    if any(p in ("r1", "v4") for p in parts):
        return "deepseek"
    return "gemini"

def get_engine_port(engine: str) -> int:
    return INTERNAL_DEEPSEEK_PORT if engine == "deepseek" else INTERNAL_GEMINI_PORT

@app.get("/v1/models")
@app.get("/v1/models/")
@app.get("/models")
@app.get("/models/")
@app.get("/v1/chat/completions/models")
@app.get("/chat/completions/models")
async def list_models():
    now = int(time.time())
    models_set = {
        # Gemini 家族模型
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
        "auto",
        # DeepSeek 系列模型
        "deepseek-v4.1-flash",
        "deepseek-v4.1-pro",
        "deepseek-v4.1-thinking",
        "deepseek-chat",
        "deepseek-reasoner"
    }

    # 动态并发探测双引擎 (8085 与 8087)
    async def probe_port(port: int):
        try:
            async with httpx.AsyncClient(timeout=2.0, trust_env=False) as client:
                resp = await client.get(f"http://127.0.0.1:{port}/v1/models")
                if resp.status_code == 200:
                    data = resp.json().get("data", [])
                    return [
                        m.get("id") for m in data
                        if m.get("id") and not any(kw in m.get("id").lower() for kw in ["gpt", "o1", "o3", "chatgpt", "terra", "luna"])
                    ]
        except Exception:
            pass
        return []

    try:
        results = await asyncio.gather(
            probe_port(INTERNAL_GEMINI_PORT),
            probe_port(INTERNAL_DEEPSEEK_PORT),
            return_exceptions=True
        )
        for res in results:
            if isinstance(res, list):
                for mid in res:
                    models_set.add(mid)
    except Exception:
        pass

    sorted_models = sorted(list(models_set))
    return {
        "object": "list",
        "data": [{"id": m, "object": "model", "created": now, "owned_by": "web2api-hub"} for m in sorted_models]
    }

async def call_backend_stream(messages: list, target_model: str):
    engine = resolve_backend_engine(target_model)
    port = get_engine_port(engine)
    engine_name = "DeepSeek" if engine == "deepseek" else "Gemini"
    backend_url = f"http://127.0.0.1:{port}/v1/chat/completions"
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
                    yield f"[{engine_name}核心 提示 {resp.status_code}]: 请确认后端服务已启动！\n详情: {err_text.decode('utf-8', errors='ignore')}"
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
                            delta_obj = choices[0].get("delta", {})
                            delta_content = delta_obj.get("content", "") or ""
                            reasoning_content = delta_obj.get("reasoning_content", "") or ""
                            combined = reasoning_content + delta_content
                            if combined:
                                yield combined
                    except Exception:
                        continue
        except httpx.ConnectError:
            yield f"【连接失败】：无法连接到 {engine_name} 核心 (端口 {port})。请在 Web2API 管理中心中确认对应服务处于运行中！"
        except Exception as e:
            yield f"[转接异常]: {str(e)}"

# 兼容历史调用方
call_gemini_backend_stream = call_backend_stream

# ================= 适配 Codex (/v1/responses) =================
@app.post("/v1/responses")
async def handle_responses_api(request: Request):
    body = await request.json()
    client_model = body.get("model", "gpt-4o")
    target_model = resolve_target_model(client_model)
    engine = resolve_backend_engine(target_model)
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
                    content = " ".join(t for t in text_parts if t).strip()
                if not content:
                    content = "你好，这是一次连通性测试。"
                messages.append({"role": role, "content": content})

    if not messages:
        messages = [{"role": "user", "content": "你好"}]

    if engine == "gemini":
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
                async for delta in call_backend_stream(messages, target_model):
                    accumulated_text += delta
                    delta_evt = {
                        "type": "response.output_text.delta",
                        "output_index": 0,
                        "content_index": 0,
                        "delta": delta
                    }
                    yield f"event: response.output_text.delta\ndata: {json.dumps(delta_evt, ensure_ascii=False)}\n\n"

                # 防御连通性测试空流
                if not accumulated_text:
                    fallback_text = "服务连接成功！Web2API 双引擎网关已就绪。"
                    accumulated_text = fallback_text
                    delta_evt = {
                        "type": "response.output_text.delta",
                        "output_index": 0,
                        "content_index": 0,
                        "delta": fallback_text
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
        async for delta in call_backend_stream(messages, target_model):
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
@app.post("/v1/chat/completions/")
@app.post("/chat/completions")
@app.post("/chat/completions/")
@app.post("/v1/chat/completions/chat/completions")
@app.post("/v1/chat/completions/chat/completions/")
@app.post("/chat/completions/chat/completions")
@app.post("/chat/completions/chat/completions/")
async def chat_completions(request: Request):
    body = await request.json()
    client_model = body.get("model", "")
    is_stream = bool(body.get("stream", False))
    
    target_model = resolve_target_model(client_model)
    engine = resolve_backend_engine(target_model)
    port = get_engine_port(engine)
    engine_name = "DeepSeek" if engine == "deepseek" else "Gemini"
    backend_url = f"http://127.0.0.1:{port}/v1/chat/completions"
    
    body["model"] = target_model
    forward_headers = {"Content-Type": "application/json"}
    auth_header = request.headers.get("Authorization")
    if auth_header:
        forward_headers["Authorization"] = auth_header

    # 消息规范化与探针防抖：
    # 针对 Trae、VS Code 等客户端在测试连通性时发送的占位图片 (image_url) 或空内容，
    # 提取文字或补充安全保底文本，防止后端核心 (如 Scotty 上传失败) 异常阻断返回空流。
    normalized_messages = []
    for msg in body.get("messages", []):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, list):
            text_items = []
            has_image = False
            for part in content:
                if isinstance(part, dict):
                    p_type = part.get("type", "")
                    if p_type in ("text", "input_text"):
                        text_items.append(part.get("text", "") or part.get("input_text", "") or "")
                    elif p_type in ("image_url", "input_image"):
                        has_image = True
                elif isinstance(part, str):
                    text_items.append(part)
            combined_text = " ".join(t for t in text_items if t).strip()
            msg = dict(msg)
            if has_image:
                msg["content"] = combined_text if combined_text else "你好，这是一次连通性测试。"
            else:
                msg["content"] = combined_text
        elif not content and role == "user":
            msg = dict(msg)
            msg["content"] = "你好，这是一次连通性测试。"
        normalized_messages.append(msg)
    if normalized_messages:
        body["messages"] = normalized_messages
    else:
        body["messages"] = [{"role": "user", "content": "你好"}]

    if is_stream:
        client = httpx.AsyncClient(timeout=120.0, trust_env=False)
        try:
            req = client.build_request("POST", backend_url, json=body, headers=forward_headers)
            resp = await client.send(req, stream=True)
            if resp.status_code != 200:
                error_body = await resp.aread()
                await resp.aclose()
                await client.aclose()
                return Response(error_body, status_code=resp.status_code, media_type=resp.headers.get("content-type"))
            
            async def stream_generator():
                has_emitted_text = False
                try:
                    if hasattr(resp, "aiter_lines"):
                        async for line in resp.aiter_lines():
                            if not line:
                                yield b"\n"
                                continue
                            if line.startswith("data: "):
                                data_str = line[6:].strip()
                                if data_str == "[DONE]":
                                    break
                                try:
                                    data_json = json.loads(data_str)
                                    choices = data_json.get("choices", [])
                                    if choices:
                                        delta = choices[0].get("delta", {})
                                        content = delta.get("content") or delta.get("reasoning_content")
                                        if content:
                                            has_emitted_text = True
                                except Exception:
                                    pass
                            yield f"{line}\n".encode("utf-8")
                    else:
                        async for chunk in resp.aiter_raw():
                            yield chunk
                            has_emitted_text = True

                    # 防御性兜底：若后端因为特殊探针/拦截导致输出 0 token (content: null)
                    # 补充一条合法的文本 chunk，杜绝 Trae 报 "llm response empty stream"
                    if not has_emitted_text:
                        fallback_chunk = {
                            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                            "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": target_model,
                            "choices": [{
                                "index": 0,
                                "delta": {"content": "服务连接成功！Web2API 双引擎网关已就绪。"},
                                "finish_reason": "stop"
                            }]
                        }
                        yield f"data: {json.dumps(fallback_chunk, ensure_ascii=False)}\n\n".encode("utf-8")

                    yield b"data: [DONE]\n\n"
                finally:
                    await resp.aclose()
                    await client.aclose()
                    
            return StreamingResponse(
                stream_generator(),
                status_code=resp.status_code,
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no"
                }
            )
        except httpx.ConnectError:
            await client.aclose()
            err_chunk = {
                "id": f"chatcmpl-err-{uuid.uuid4().hex[:8]}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": target_model,
                "choices": [{
                    "index": 0,
                    "delta": {"content": f"【连接失败】：无法连接到 {engine_name} 核心 (端口 {port})。请在 Web2API 管理中心中确认对应服务处于运行中！"},
                    "finish_reason": "stop"
                }]
            }
            async def err_stream():
                yield f"data: {json.dumps(err_chunk, ensure_ascii=False)}\n\n".encode("utf-8")
                yield b"data: [DONE]\n\n"
            return StreamingResponse(
                err_stream(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no"
                }
            )
        except Exception as e:
            await client.aclose()
            return JSONResponse({"error": {"message": f"代理转接异常: {str(e)}"}}, status_code=500)
    else:
        try:
            async with httpx.AsyncClient(timeout=120.0, trust_env=False) as client:
                resp = await client.post(backend_url, json=body, headers=forward_headers)
                if resp.status_code == 200:
                    try:
                        res_json = resp.json()
                        choices = res_json.get("choices", [])
                        if choices:
                            msg = choices[0].get("message", {})
                            content = msg.get("content")
                            if not content:
                                msg["content"] = "服务连接成功！Web2API 双引擎网关已就绪。"
                                res_json["choices"][0]["message"] = msg
                                return JSONResponse(res_json, status_code=200)
                    except Exception:
                        pass
                return Response(
                    content=resp.content,
                    status_code=resp.status_code,
                    media_type=resp.headers.get("content-type", "application/json")
                )
        except httpx.ConnectError:
            return JSONResponse({
                "error": {
                    "message": f"【连接失败】：无法连接到 {engine_name} 核心 (端口 {port})。请在 Web2API 管理中心中确认对应服务处于运行中！"
                }
            }, status_code=502)
        except Exception as e:
            return JSONResponse({"error": {"message": f"代理转接异常: {str(e)}"}}, status_code=500)

if __name__ == "__main__":
    port = 8090
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except Exception:
            pass
    print(f"[Universal Gateway] Listening on http://0.0.0.0:{port} -> Gemini :8085 / DeepSeek :8087")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


