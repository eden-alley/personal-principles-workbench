import json
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import Mock, patch

from integrations.librechat.organizer import OrganizerModel
from integrations.librechat.provider_config import (
    ProviderConfigError,
    load_provider_config,
    request_api_completion,
)


class _ApiHandler(BaseHTTPRequestHandler):
    requests = []

    def log_message(self, *_args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        self.__class__.requests.append((dict(self.headers), json.loads(raw)))
        payload = {
            "id": "chatcmpl-test",
            "model": "test-model",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "整理结果"}, "finish_reason": "stop"}],
        }
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class _SseApiHandler(_ApiHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        payload = json.loads(raw)
        self.__class__.requests.append((dict(self.headers), payload))
        chunks = [
            {"id": "chatcmpl-sse", "model": "provider-model", "choices": [{"index": 0, "delta": {"role": "assistant", "content": "可见答案"}, "finish_reason": None}]},
            {"id": "chatcmpl-sse", "model": "provider-model", "choices": [{"index": 0, "delta": {"content": "<memory-proposal>"}, "finish_reason": None}]},
            {"id": "chatcmpl-sse", "model": "provider-model", "choices": [{"index": 0, "delta": {"content": '{"kind":"principle","text":"候选"}</memory-proposal>'}, "finish_reason": "stop"}]},
            "[DONE]",
        ]
        encoded = b"".join(b"data: " + json.dumps(item, ensure_ascii=False).encode() + b"\n\n" if item != "[DONE]" else b"data: [DONE]\n\n" for item in chunks)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class ProviderConfigTests(unittest.TestCase):
    def setUp(self):
        _ApiHandler.requests = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _ApiHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(1)

    def env(self, **updates):
        values = {
            "LIBRARY_PROVIDER": "api",
            "LIBRARY_API_BASE_URL": f"http://127.0.0.1:{self.server.server_port}/v1",
            "LIBRARY_API_KEY": "test-secret",
            "LIBRARY_API_MODEL": "test-model",
        }
        values.update(updates)
        return values

    def test_default_provider_is_codex_and_api_credentials_are_not_in_repr_or_payload(self):
        config = load_provider_config({})
        self.assertEqual(config.provider, "codex")
        api_config = load_provider_config(self.env())
        self.assertNotIn("test-secret", repr(api_config))
        self.assertNotIn("test-secret", json.dumps(api_config.api_payload([{"role": "user", "content": "x"}])))

    def test_api_requires_all_credentials_and_model(self):
        for missing in ("LIBRARY_API_BASE_URL", "LIBRARY_API_KEY", "LIBRARY_API_MODEL"):
            values = self.env()
            values.pop(missing)
            with self.subTest(missing=missing), self.assertRaises(ProviderConfigError) as error:
                load_provider_config(values)
            self.assertIn(missing, str(error.exception))

    def test_api_url_requires_https_except_loopback(self):
        config = load_provider_config(self.env())
        self.assertEqual(config.api_scheme, "http")
        with self.assertRaises(ProviderConfigError):
            load_provider_config(self.env(LIBRARY_API_BASE_URL="http://api.example/v1"))
        with self.assertRaises(ProviderConfigError):
            load_provider_config(self.env(LIBRARY_API_BASE_URL="https://api.example/v1?key=leak"))

    def test_api_request_is_nonstream_whitelisted_and_uses_bearer_header(self):
        config = load_provider_config(self.env())
        response = request_api_completion(config, [{"role": "user", "content": [{"type": "text", "text": "看图"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}]}])
        self.assertEqual(response["choices"][0]["message"]["content"], "整理结果")
        headers, payload = _ApiHandler.requests[-1]
        self.assertEqual(payload, {"messages": [{"role": "user", "content": [{"type": "text", "text": "看图"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}]}], "model": "test-model", "stream": False})
        self.assertEqual(headers["Authorization"], "Bearer test-secret")
        self.assertNotIn("reasoning_effort", payload)
        self.assertNotIn("owner", payload)

    def test_organizer_api_lane_does_not_construct_sdk_bridge(self):
        config = load_provider_config(self.env())
        with patch("integrations.librechat.organizer.LibreChatBridge", side_effect=AssertionError("SDK bridge must stay independent")):
            model = OrganizerModel(config)
        try:
            self.assertEqual(model("请整理"), "整理结果")
        finally:
            model.close()

    def test_codex_lane_keeps_independent_bridge(self):
        config = load_provider_config({})
        fake = type("Bridge", (), {
            "validate_request": lambda self, request: request,
            "open_request": lambda self, request: type("Session", (), {"closed": False, "release": lambda self, success: None})(),
            "run_nonstream": lambda self, session: {"choices": [{"message": {"content": "SDK 结果"}}]},
            "close": lambda self: None,
        })()
        with patch("integrations.librechat.organizer.LibreChatBridge", return_value=fake) as bridge:
            model = OrganizerModel(config)
        try:
            self.assertEqual(model("请整理"), "SDK 结果")
            bridge.assert_called_once_with()
        finally:
            model.close()


class GatewaySseTests(unittest.TestCase):
    def test_api_foreground_uses_sse_model_override_and_proposal_filter(self):
        _SseApiHandler.requests = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), _SseApiHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client_socket, peer_socket = socket.socketpair()
        try:
            config = load_provider_config({
                "LIBRARY_PROVIDER": "api",
                "LIBRARY_API_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1",
                "LIBRARY_API_KEY": "api-secret",
                "LIBRARY_API_MODEL": "configured-api-model",
            })
            from integrations.librechat.gateway import Handler
            import os
            os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
            import django
            django.setup()

            handler = Handler.__new__(Handler)
            handler.owner = "a" * 24
            handler.headers = {
                "X-Library-Conversation": "chat",
                "X-Library-Message": "message",
            }
            handler.connection = client_socket
            handler.server = SimpleNamespace(provider_config=config, token="internal-secret")
            handler.respond = Mock()
            handler.start_stream = Mock()
            events = []
            handler.event = events.append
            prepared = {"messages": [{"role": "user", "content": "问题"}], "replayed": False, "lease_token": "lease"}
            with patch("workbench.services.library_gateway.prepare", return_value=prepared), patch("workbench.services.library_gateway.finish") as finish:
                handler.chat({"model": "frontend-model", "stream": True, "reasoning_effort": "high", "messages": prepared["messages"]})
            headers, payload = _SseApiHandler.requests[-1]
            self.assertEqual(payload["model"], "configured-api-model")
            self.assertTrue(payload["stream"])
            self.assertNotIn("reasoning_effort", payload)
            self.assertNotIn("owner", payload)
            self.assertEqual(headers["Authorization"], "Bearer api-secret")
            self.assertNotIn("internal-secret", headers.values())
            rendered_events = "".join(event.decode("utf-8") if isinstance(event, bytes) else str(event) for event in events)
            self.assertNotIn("<memory-proposal>", rendered_events)
            finish.assert_called_once()
            self.assertEqual(finish.call_args.args[1], "可见答案")
            self.assertEqual(finish.call_args.args[2], {"kind": "principle", "text": "候选"})
        finally:
            client_socket.close()
            peer_socket.close()
            server.shutdown()
            server.server_close()
            thread.join(1)


if __name__ == "__main__":
    unittest.main()
