from __future__ import annotations

import base64
from io import BytesIO
import json
import os
import threading
import time
import unittest
from http.client import HTTPConnection
from types import SimpleNamespace
from unittest import mock

from integrations.librechat import bridge


PNG_1X1 = (
    "data:image/png;base64,"
    + "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    + "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _event(method, payload):
    return SimpleNamespace(method=method, payload=payload)


class FakeHandle:
    def __init__(self, text="hello", error=None, delay=0):
        self.text = text
        self.error = error
        self.delay = delay
        self.interrupted = False

    def stream(self):
        if self.delay:
            time.sleep(self.delay)
        if self.error:
            yield _event("turn/completed", {"turn": {"status": "failed", "error": self.error}})
            return
        yield _event("item/agentMessage/delta", {"delta": self.text})
        yield _event("item/completed", {"item": {"type": "agentMessage", "phase": "final_answer", "text": self.text}})
        yield _event("turn/completed", {"turn": {"status": "completed"}})

    def interrupt(self):
        self.interrupted = True


class FakeThread:
    def __init__(self, client, directory, handle):
        self.client = client
        self.directory = directory
        self.handle = handle
        self.inputs = None
        self.effort = None

    def read(self, **kwargs):
        return SimpleNamespace(model_dump=lambda **kw: {"thread": {"cwd": self.directory, "ephemeral": True}})

    def turn(self, inputs, effort=None):
        self.inputs = inputs
        self.effort = effort
        self.client.threads.append(self)
        return self.handle


class FakeClient:
    instances = []
    next_handles = []

    def __init__(self, config):
        self.config = config
        self.closed = False
        self.threads = []
        self._client = SimpleNamespace(request=self.request)
        self.__class__.instances.append(self)

    def request(self, method, payload, response_model=None):
        settings = {
            "mcp_servers": {"inherited": {"enabled": False}},
            "features": {**{name: False for name in bridge.codex_provider.DISABLED_FEATURES}, "skip_host_skill_discovery": True},
            "sandbox_mode": "read-only",
            "approval_policy": "never",
            "web_search": "disabled",
            "skills": {"include_instructions": False, "bundled": {"enabled": False}},
            "history": {"persistence": "none"},
            "project_doc_max_bytes": 0,
        }
        return SimpleNamespace(model_dump=lambda **kw: {"config": settings})

    def thread_start(self, **kwargs):
        handle = self.__class__.next_handles.pop(0) if self.__class__.next_handles else FakeHandle()
        return FakeThread(self, kwargs["cwd"], handle)

    def close(self):
        self.closed = True


class BridgeTests(unittest.TestCase):
    def setUp(self):
        FakeClient.instances = []
        FakeClient.next_handles = []
        self.env = mock.patch.dict(os.environ, {"LIBRECHAT_BRIDGE_TOKEN": "test-token"}, clear=False)
        self.env.start()
        self.auth = mock.patch.object(bridge.codex_auth, "status", return_value={"code": "connected"})
        self.auth.start()
        self.codex = mock.patch.object(bridge.codex_provider, "Codex", FakeClient)
        self.codex.start()
        self.server_bridge = bridge.LibreChatBridge()
        self.server = bridge.create_server("127.0.0.1", 0, self.server_bridge)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server_bridge.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(1)
        self.codex.stop()
        self.auth.stop()
        self.env.stop()

    def request(self, method, path, body=None, headers=None):
        conn = HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        payload = None if body is None else json.dumps(body).encode()
        request_headers = {"Authorization": "Bearer test-token", "Host": "127.0.0.1"}
        if payload is not None:
            request_headers["Content-Type"] = "application/json"
        request_headers.update(headers or {})
        conn.request(method, path, payload, request_headers)
        response = conn.getresponse()
        raw = response.read()
        conn.close()
        return response.status, dict(response.headers), raw

    def test_text_image_order_and_fresh_threads_reuse_warm_client(self):
        body = {
            "model": "gpt-6.1-sol",
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "before"},
                {"type": "image_url", "image_url": {"url": PNG_1X1}},
                {"type": "text", "text": "after"},
            ]}],
        }
        status, _, raw = self.request("POST", "/v1/chat/completions", body)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["choices"][0]["message"]["content"], "hello")
        status, _, raw = self.request("POST", "/v1/chat/completions", body)
        self.assertEqual(status, 200)
        self.assertEqual(len(FakeClient.instances), 2)  # discovery + warm client
        self.assertEqual(len(FakeClient.instances[-1].threads), 2)
        inputs = FakeClient.instances[-1].threads[0].inputs
        self.assertEqual([type(item).__name__ for item in inputs], ["TextInput", "TextInput", "ImageInput", "TextInput"])
        self.assertEqual([getattr(item, "text", None) for item in inputs], ["[user]", "before", None, "after"])

    def test_auth_origin_remote_image_and_bad_image_are_rejected(self):
        body = {"model": "gpt-6.1-sol", "messages": [{"role": "user", "content": "x"}]}
        status, _, _ = self.request("POST", "/v1/chat/completions", body, {"Authorization": "Bearer wrong"})
        self.assertEqual(status, 401)
        status, _, _ = self.request("POST", "/v1/chat/completions", body, {"Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        remote = {"model": "gpt-6.1-sol", "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://example.com/a.png"}}]}]}
        status, _, _ = self.request("POST", "/v1/chat/completions", remote)
        self.assertEqual(status, 400)
        bad = {"model": "gpt-6.1-sol", "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,not-base64"}}]}]}
        status, _, _ = self.request("POST", "/v1/chat/completions", bad)
        self.assertEqual(status, 400)

    def test_tool_fields_and_non_boolean_stream_are_rejected(self):
        body = {"model": "gpt-6.1-sol", "stream": "false", "tools": [], "messages": [{"role": "user", "content": "x"}]}
        status, _, raw = self.request("POST", "/v1/chat/completions", body)
        self.assertEqual(status, 400)
        self.assertIn("工具", raw.decode())

    def test_stream_is_openai_sse_and_quota_is_error_envelope(self):
        body = {"model": "gpt-6.1-sol", "stream": True, "messages": [{"role": "user", "content": "x"}]}
        status, headers, raw = self.request("POST", "/v1/chat/completions", body)
        self.assertEqual(status, 200)
        self.assertIn("text/event-stream", headers["Content-Type"])
        text = raw.decode()
        self.assertIn('"delta":{"content":"hello","role":"assistant"}', text)
        self.assertIn('"finish_reason":"stop"', text)
        self.assertTrue(text.endswith("data: [DONE]\n\n"))

        FakeClient.next_handles = [FakeHandle(error={"codexErrorInfo": "usageLimitExceeded"})]
        status, _, raw = self.request("POST", "/v1/chat/completions", body)
        self.assertEqual(status, 200)
        self.assertIn("额度", raw.decode())

    def test_turn_failed_event_preserves_quota_error_envelope(self):
        handle = FakeHandle()
        handle.stream = lambda: iter([_event("turn/failed", {"error": {"codexErrorInfo": "rateLimitExceeded"}})])
        FakeClient.next_handles = [handle]
        body = {"model": "gpt-6.1-sol", "stream": True, "messages": [{"role": "user", "content": "x"}]}
        status, _, raw = self.request("POST", "/v1/chat/completions", body)
        self.assertEqual(status, 200)
        self.assertIn("额度", raw.decode())

    def test_busy_returns_429_and_cancelled_request_releases_for_next_request(self):
        first = FakeHandle(delay=0.3)
        FakeClient.next_handles = [first]
        body = {"model": "gpt-6.1-sol", "stream": True, "messages": [{"role": "user", "content": "x"}]}
        ready = threading.Event()
        result = {}

        def run_first():
            ready.set()
            result["response"] = self.request("POST", "/v1/chat/completions", body)

        worker = threading.Thread(target=run_first)
        worker.start()
        ready.wait(1)
        time.sleep(0.03)
        status, _, _ = self.request("POST", "/v1/chat/completions", body)
        self.assertEqual(status, 429)
        worker.join(1)
        self.assertIn("response", result)
        status, _, _ = self.request("POST", "/v1/chat/completions", body)
        self.assertEqual(status, 200)

    def test_broken_stream_closes_warm_client_and_next_request_can_use_bridge(self):
        session = self.server_bridge.open_request(self.server_bridge.validate_request({
            "model": "gpt-6.1-sol", "stream": True, "messages": [{"role": "user", "content": "x"}],
        }))
        self.server_bridge.stream_to(session, lambda _data: (_ for _ in ()).throw(BrokenPipeError()))
        self.assertTrue(all(instance.closed for instance in FakeClient.instances))
        status, _, _ = self.request("POST", "/v1/chat/completions", {"model": "gpt-6.1-sol", "messages": [{"role": "user", "content": "x"}]})
        self.assertEqual(status, 200)

    def test_malformed_json_has_explicit_request_error(self):
        conn = HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        conn.request("POST", "/v1/chat/completions", b"{", {"Authorization": "Bearer test-token", "Host": "127.0.0.1", "Content-Type": "application/json", "Content-Length": "1"})
        response = conn.getresponse()
        raw = response.read().decode()
        conn.close()
        self.assertEqual(response.status, 400)
        self.assertIn("JSON", raw)

    def test_auth_status_is_inside_request_deadline(self):
        original = bridge.codex_auth.status

        def hung_status(**kwargs):
            time.sleep(0.4)
            return {"code": "connected"}

        bridge.codex_auth.status = hung_status
        try:
            request = self.server_bridge.validate_request({"model": "gpt-6.1-sol", "timeout": 0.03, "messages": [{"role": "user", "content": "x"}]})
            started = time.monotonic()
            with self.assertRaises(bridge.BridgeError) as error:
                self.server_bridge.open_request(request)
            self.assertEqual(error.exception.code, "timeout")
            self.assertLess(time.monotonic() - started, 0.2)
        finally:
            bridge.codex_auth.status = original

    def test_preparation_failure_discards_warm_runtime_before_next_request(self):
        original = FakeClient.thread_start
        failed = {"value": True}

        def fail_once(self, **kwargs):
            if failed["value"]:
                failed["value"] = False
                raise RuntimeError("setup failed")
            return original(self, **kwargs)

        FakeClient.thread_start = fail_once
        try:
            request = self.server_bridge.validate_request({"model": "gpt-6.1-sol", "messages": [{"role": "user", "content": "x"}]})
            with self.assertRaises(bridge.BridgeError):
                self.server_bridge.open_request(request)
            self.assertTrue(all(instance.closed for instance in FakeClient.instances))
            status, _, _ = self.request("POST", "/v1/chat/completions", {"model": "gpt-6.1-sol", "messages": [{"role": "user", "content": "x"}]})
            self.assertEqual(status, 200)
        finally:
            FakeClient.thread_start = original

    def test_preparation_time_counts_against_stream_deadline(self):
        original = FakeClient.thread_start

        def slow_start(self, **kwargs):
            time.sleep(0.04)
            return original(self, **kwargs)

        FakeClient.thread_start = slow_start
        FakeClient.next_handles = [FakeHandle(delay=0.08)]
        try:
            request = self.server_bridge.validate_request({"model": "gpt-6.1-sol", "stream": True, "timeout": 0.06, "messages": [{"role": "user", "content": "x"}]})
            session = self.server_bridge.open_request(request)
            started = time.monotonic()
            self.server_bridge.stream_to(session, lambda _data: None)
            self.assertLess(time.monotonic() - started, 0.15)
            self.assertTrue(all(instance.closed for instance in FakeClient.instances))
        finally:
            FakeClient.thread_start = original

    def test_non_finite_timeout_is_rejected_and_health_does_not_refresh_auth(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(bridge.BridgeError):
                self.server_bridge.validate_request({"model": "gpt-6.1-sol", "timeout": value, "messages": [{"role": "user", "content": "x"}]})
        self.server_bridge._auth_cache = None
        with mock.patch.object(bridge.codex_auth, "status", side_effect=AssertionError("health must not refresh")):
            health = self.server_bridge.health()
        self.assertFalse(health["connected"])

    def test_disconnect_during_sse_headers_releases_open_session(self):
        session = SimpleNamespace(closed=False, release=mock.Mock())
        raw = json.dumps({"model": "gpt-6.1-sol", "stream": True, "messages": [{"role": "user", "content": "x"}]}).encode()
        fake_handler = SimpleNamespace(
            bridge=self.server_bridge,
            path="/v1/chat/completions",
            headers={"Content-Length": str(len(raw))},
            rfile=BytesIO(raw),
            _authorized=lambda: True,
            send_response=mock.Mock(side_effect=ConnectionResetError()),
            _error=mock.Mock(),
        )
        with mock.patch.object(self.server_bridge, "open_request", return_value=session):
            bridge._Handler.do_POST(fake_handler)
        session.release.assert_called_once_with(False)


if __name__ == "__main__":
    unittest.main()
