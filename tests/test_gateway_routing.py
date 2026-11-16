import os
import sys
import json
import unittest
import asyncio
from unittest.mock import patch, MagicMock

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# 动态探测并兼容环境中的依赖路径
for extra_path in [
    os.path.join(BASE_DIR, "deepseek_venv", "Lib", "site-packages"),
    r"E:\Hermes\hermes-agent\venv\Lib\site-packages"
]:
    if os.path.exists(extra_path) and extra_path not in sys.path:
        sys.path.append(extra_path)

import gateway_server

class TestGatewayRouting(unittest.TestCase):
    def test_ports_defined(self):
        """1. 检查内部端口常量定义"""
        self.assertEqual(getattr(gateway_server, "INTERNAL_GEMINI_PORT", None), 8085)
        self.assertEqual(getattr(gateway_server, "INTERNAL_DEEPSEEK_PORT", None), 8087)

    def test_resolve_backend_engine_decision(self):
        """2. 验证多引擎分流函数 resolve_backend_engine 的判断逻辑"""
        resolve_backend = getattr(gateway_server, "resolve_backend_engine", None)
        self.assertIsNotNone(resolve_backend, "resolve_backend_engine function must exist")

        # DeepSeek 系列
        self.assertEqual(resolve_backend("deepseek-v4.1-flash"), "deepseek")
        self.assertEqual(resolve_backend("deepseek-v4.1-pro"), "deepseek")
        self.assertEqual(resolve_backend("deepseek-v4.1-thinking"), "deepseek")
        self.assertEqual(resolve_backend("deepseek-chat"), "deepseek")
        self.assertEqual(resolve_backend("deepseek-reasoner"), "deepseek")
        self.assertEqual(resolve_backend("r1"), "deepseek")
        self.assertEqual(resolve_backend("v4"), "deepseek")
        self.assertEqual(resolve_backend("deepseek-r1"), "deepseek")
        self.assertEqual(resolve_backend("DEEPSEEK-CHAT"), "deepseek")

        # Gemini 家族与兜底
        self.assertEqual(resolve_backend("gemini-4-hydrogen"), "gemini")
        self.assertEqual(resolve_backend("gemini-3.8-flash"), "gemini")
        self.assertEqual(resolve_backend("argon"), "gemini")
        self.assertEqual(resolve_backend("hydrogen"), "gemini")
        self.assertEqual(resolve_backend("gemini-flash-lite"), "gemini")
        self.assertEqual(resolve_backend("auto"), "gemini")
        self.assertEqual(resolve_backend(""), "gemini")
        self.assertEqual(resolve_backend(None), "gemini")

    def test_models_aggregation(self):
        """3. 验证 /v1/models 同时聚合 Gemini 与 DeepSeek 系列模型"""
        data = asyncio.run(gateway_server.list_models())
        self.assertEqual(data.get("object"), "list")
        ids = {m["id"] for m in data.get("data", [])}

        expected_deepseek = {
            "deepseek-v4.1-flash",
            "deepseek-v4.1-pro",
            "deepseek-v4.1-thinking",
            "deepseek-chat",
            "deepseek-reasoner"
        }
        expected_gemini = {
            "gemini-4-hydrogen",
            "gemini-3.8-flash",
            "gemini-flash-lite",
            "argon"
        }

        self.assertTrue(expected_deepseek.issubset(ids), f"Missing deepseek models: {expected_deepseek - ids}")
        self.assertTrue(expected_gemini.issubset(ids), f"Missing gemini models: {expected_gemini - ids}")

    def test_call_backend_stream_reasoning_support(self):
        """4. 验证 call_backend_stream 对 DeepSeek reasoning_content 的平滑支持"""
        call_stream_func = getattr(gateway_server, "call_backend_stream", getattr(gateway_server, "call_gemini_backend_stream", None))
        self.assertIsNotNone(call_stream_func, "Stream helper function must exist")

        # 构造模拟的 SSE 响应，包含 reasoning_content 与 content
        class MockResponse:
            status_code = 200
            async def __aenter__(self):
                return self
            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass
            async def aiter_lines(self):
                # 思考流片段
                chunk1 = {
                    "choices": [{
                        "delta": {"reasoning_content": "正在思考问题..."}
                    }]
                }
                # 正文内容片段
                chunk2 = {
                    "choices": [{
                        "delta": {"content": "这是最终答案。"}
                    }]
                }
                yield f"data: {json.dumps(chunk1)}"
                yield f"data: {json.dumps(chunk2)}"
                yield "data: [DONE]"

        class MockClient:
            async def __aenter__(self):
                return self
            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass
            def stream(self, method, url, **kwargs):
                self.target_url = url
                return MockResponse()

        with patch("gateway_server.httpx.AsyncClient", return_value=MockClient()):
            async def collect():
                out = []
                async for item in call_stream_func([{"role": "user", "content": "hi"}], "deepseek-v4.1-thinking"):
                    out.append(item)
                return "".join(out)

            result = asyncio.run(collect())
            self.assertIn("正在思考问题...", result)
            self.assertIn("这是最终答案。", result)

    def test_backend_down_chinese_error_message(self):
        """5. 验证后端服务不可用时，返回清晰定位的中文提示"""
        # 测试非流式 deepseek 失败提示
        class DummyRequest:
            def __init__(self, body):
                self._body = body
                self.headers = {}
            async def json(self):
                return self._body

        req_deepseek = DummyRequest({
            "model": "deepseek-v4.1-flash",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False
        })

        # 当端口未开启时（或者 mock ConnectError）
        import httpx
        with patch("gateway_server.get_current_model_choice", return_value="auto"), \
             patch("gateway_server.httpx.AsyncClient.post", side_effect=httpx.ConnectError("Connection refused")):
            resp = asyncio.run(gateway_server.chat_completions(req_deepseek))
            self.assertEqual(resp.status_code, 502)
            data = json.loads(resp.body.decode("utf-8"))
            err_msg = data.get("error", {}).get("message", "")
            self.assertIn("DeepSeek", err_msg)
            self.assertIn("8087", err_msg)

        req_gemini = DummyRequest({
            "model": "gemini-3.8-flash",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False
        })
        with patch("gateway_server.get_current_model_choice", return_value="auto"), \
             patch("gateway_server.httpx.AsyncClient.post", side_effect=httpx.ConnectError("Connection refused")):
            resp = asyncio.run(gateway_server.chat_completions(req_gemini))
            self.assertEqual(resp.status_code, 502)
            data = json.loads(resp.body.decode("utf-8"))
            err_msg = data.get("error", {}).get("message", "")
            self.assertIn("Gemini", err_msg)
            self.assertIn("8085", err_msg)

    def test_dynamic_models_probe_aggregation(self):
        """6. 验证同时探测双引擎并合并动态模型列表"""
        class MockProbeResponse:
            def __init__(self, port):
                self.port = port
                self.status_code = 200
            def json(self):
                if self.port == 8087:
                    return {"data": [{"id": "deepseek-coder-v2"}]}
                else:
                    return {"data": [{"id": "gemini-ultra-code"}]}

        class MockProbeClient:
            async def __aenter__(self):
                return self
            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass
            async def get(self, url, **kwargs):
                if ":8087" in url:
                    return MockProbeResponse(8087)
                return MockProbeResponse(8085)

        with patch("gateway_server.httpx.AsyncClient", return_value=MockProbeClient()):
            data = asyncio.run(gateway_server.list_models())
            ids = {m["id"] for m in data.get("data", [])}
            self.assertIn("deepseek-coder-v2", ids)
            self.assertIn("gemini-ultra-code", ids)
            self.assertIn("deepseek-v4.1-flash", ids)

    def test_codex_responses_api_with_deepseek(self):
        """7. 验证 /v1/responses (Codex 协议) 对 DeepSeek 流式输出与思考链的适配"""
        class DummyRequest:
            def __init__(self, body):
                self._body = body
            async def json(self):
                return self._body

        req_codex = DummyRequest({
            "model": "deepseek-v4.1-thinking",
            "input": "请写一个快速排序",
            "stream": True
        })

        # 模拟 call_backend_stream 输出思考片段与代码正文
        async def mock_backend_stream(messages, target_model):
            yield "【思考中】先选基准点"
            yield "def quicksort(arr): return arr"

        with patch("gateway_server.call_backend_stream", side_effect=mock_backend_stream):
            resp = asyncio.run(gateway_server.handle_responses_api(req_codex))
            self.assertEqual(resp.media_type, "text/event-stream")

            async def read_stream(streaming_resp):
                chunks = []
                async for chunk in streaming_resp.body_iterator:
                    if isinstance(chunk, bytes):
                        chunks.append(chunk.decode("utf-8"))
                    else:
                        chunks.append(chunk)
                return "".join(chunks)

            raw_events = asyncio.run(read_stream(resp))
            self.assertIn("response.created", raw_events)
            self.assertIn("response.output_text.delta", raw_events)
            self.assertIn("【思考中】先选基准点", raw_events)
            self.assertIn("def quicksort(arr): return arr", raw_events)
            self.assertIn("response.completed", raw_events)
            self.assertIn("[DONE]", raw_events)

    def test_chat_completions_stream_connect_error(self):
        """8. 验证 /v1/chat/completions 流式模式下后端未启动时的 SSE 错误提示"""
        class DummyRequest:
            def __init__(self, body):
                self._body = body
                self.headers = {}
            async def json(self):
                return self._body

        req_stream = DummyRequest({
            "model": "deepseek-v4.1-flash",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True
        })

        import httpx
        with patch("gateway_server.httpx.AsyncClient.send", side_effect=httpx.ConnectError("Connection refused")):
            resp = asyncio.run(gateway_server.chat_completions(req_stream))
            self.assertEqual(resp.media_type, "text/event-stream")

            async def read_stream(streaming_resp):
                chunks = []
                async for chunk in streaming_resp.body_iterator:
                    if isinstance(chunk, bytes):
                        chunks.append(chunk.decode("utf-8"))
                    else:
                        chunks.append(chunk)
                return "".join(chunks)

            stream_out = asyncio.run(read_stream(resp))
            self.assertIn("DeepSeek", stream_out)
            self.assertIn("8087", stream_out)
            self.assertIn("[DONE]", stream_out)

    def test_chat_completions_stream_success(self):
        """9. 验证 /v1/chat/completions 流式模式正常转发并保持连接产出 chunk"""
        class DummyRequest:
            def __init__(self, body):
                self._body = body
                self.headers = {"Authorization": "Bearer test-key"}
            async def json(self):
                return self._body

        req_stream = DummyRequest({
            "model": "gemini-4-hydrogen",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True
        })

        class MockStreamResponse:
            status_code = 200
            headers = {"content-type": "text/event-stream"}
            async def aiter_raw(self):
                yield b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
                yield b'data: [DONE]\n\n'
            async def aclose(self):
                pass

        class MockClient:
            def __init__(self, *args, **kwargs):
                self.is_closed = False
            def build_request(self, method, url, **kwargs):
                return None
            async def send(self, req, stream=False):
                return MockStreamResponse()
            async def aclose(self):
                self.is_closed = True

        mock_client = MockClient()
        with patch("gateway_server.httpx.AsyncClient", return_value=mock_client):
            resp = asyncio.run(gateway_server.chat_completions(req_stream))
            self.assertEqual(resp.media_type, "text/event-stream")

            async def read_stream(streaming_resp):
                chunks = []
                async for chunk in streaming_resp.body_iterator:
                    if isinstance(chunk, bytes):
                        chunks.append(chunk.decode("utf-8"))
                    else:
                        chunks.append(chunk)
                return "".join(chunks)

            output = asyncio.run(read_stream(resp))
            self.assertIn("Hello", output)
            self.assertIn("[DONE]", output)
            self.assertTrue(mock_client.is_closed)

if __name__ == "__main__":
    unittest.main()
