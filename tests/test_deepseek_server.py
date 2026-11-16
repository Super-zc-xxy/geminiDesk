import os
import sys
import json
import unittest
import asyncio

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from deepseek_server import app, list_models, chat_completions, solve_pow, get_token, hash_v1

class TestDeepSeekServer(unittest.TestCase):
    def test_models_endpoint(self):
        """Verify GET /v1/models returns the expected DeepSeek model IDs."""
        data = asyncio.run(list_models())
        self.assertEqual(data.get("object"), "list")
        ids = {m["id"] for m in data.get("data", [])}
        expected_ids = {
            "deepseek-v4.1-flash",
            "deepseek-v4.1-pro",
            "deepseek-v4.1-thinking",
            "deepseek-chat",
            "deepseek-reasoner",
        }
        self.assertTrue(expected_ids.issubset(ids), f"Missing models: {expected_ids - ids}")

    def test_wasm_pow_solver(self):
        """Verify WASM PoW solver correctly resolves DeepSeekHashV1 challenge."""
        prefix = "test_salt_1735000000_"
        expected_answer = 42
        target_challenge = hash_v1(f"{prefix}{expected_answer}")
        self.assertEqual(len(target_challenge), 64)
        
        solved = solve_pow(target_challenge, prefix, 1000.0)
        self.assertEqual(solved, expected_answer)

    def test_missing_or_placeholder_token_graceful_handling(self):
        """When deepseek_token.txt has placeholder or empty token, server gracefully informs user."""
        from unittest.mock import patch

        # Create dummy Request for non-streaming
        class DummyRequest:
            def __init__(self, body_dict):
                self._body = body_dict
            async def json(self):
                return self._body

        req_non_stream = DummyRequest({
            "model": "deepseek-v4.1-flash",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False
        })
        with patch("deepseek_server.get_token", return_value=None):
            resp = asyncio.run(chat_completions(req_non_stream))
            # Non-streaming returns JSONResponse or dict
            if hasattr(resp, "body"):
                data = json.loads(resp.body.decode("utf-8"))
            elif isinstance(resp, dict):
                data = resp
            else:
                self.fail(f"Unexpected response type: {type(resp)}")
            
            self.assertIn("choices", data)
            content = data["choices"][0]["message"]["content"]
            self.assertIn("deepseek_token.txt", content)

        # Stream request
        req_stream = DummyRequest({
            "model": "deepseek-v4.1-flash",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True
        })
        with patch("deepseek_server.get_token", return_value=None):
            resp_stream = asyncio.run(chat_completions(req_stream))
            # Streaming returns StreamingResponse
            async def collect_stream(streaming_resp):
                chunks = []
                async for chunk in streaming_resp.body_iterator:
                    if isinstance(chunk, bytes):
                        chunks.append(chunk.decode("utf-8"))
                    else:
                        chunks.append(chunk)
                return "".join(chunks)

            stream_text = asyncio.run(collect_stream(resp_stream))
            self.assertIn("deepseek_token.txt", stream_text)
            self.assertIn("data: [DONE]", stream_text)

if __name__ == "__main__":
    unittest.main()
