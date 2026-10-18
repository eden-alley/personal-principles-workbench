import json
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock


from workbench.ai.base import ProviderError
from workbench.ai import codex_auth, codex_provider


def context():
    return {
        "source": {"id": 1, "text": "当信息不足时先补充事实。", "transcript": "", "url": ""},
        "versions": [],
        "values": [],
        "observations": [],
        "messages": [{"role": "user", "content": "我想减少仓促决定。"}],
        "situation": "项目延期",
        "depth": "daily",
    }


def valid_payload():
    return {
        "principle": {
            "title": "先补充事实",
            "statement": "当信息不足时先补充事实。",
            "kind": "strategy",
            "when_to_use": "信息不足时",
            "conditions": ["信息不足"],
            "boundaries": ["紧急情况先止损"],
            "questions": ["还缺什么事实？"],
            "tags": ["决策"],
        },
        "reason": "待校对整理",
        "recommendation": "请人工确认后保存",
        "citations": {"versions": [], "observations": []},
    }


class Event:
    def __init__(self, method, payload):
        self.method = method
        self.payload = SimpleNamespace(model_dump=lambda **kwargs: payload)


class FakeHandle:
    def __init__(self, events, delay=0):
        self.events = events
        self.delay = delay
        self.interrupted = False

    def stream(self):
        if self.delay:
            time.sleep(self.delay)
        yield from self.events

    def interrupt(self):
        self.interrupted = True


class FakeThread:
    def __init__(self, handle, directory):
        self.directory = directory
        self.handle = handle
        self.prompts = []
        self.turn_kwargs = {}

    def read(self, **kwargs):
        return SimpleNamespace(model_dump=lambda **kw: {"thread": {"cwd": self.directory, "ephemeral": True}})

    def turn(self, prompt, **kwargs):
        self.turn_kwargs = kwargs
        self.prompts.append(prompt)
        return self.handle


class FakeCodex:
    handles = []
    configs = []
    threads = []
    instances = []
    safe_settings = True

    def __init__(self, config):
        self.config = config
        self.closed = False
        FakeCodex.instances.append(self)
        mcp_disabled = any(item.startswith("mcp_servers=") for item in config.config_overrides)
        settings = {
            "mcp_servers": {"inherited": {"enabled": not mcp_disabled}},
            "features": {**{name: False for name in codex_provider.DISABLED_FEATURES}, "skip_host_skill_discovery": True},
            "sandbox_mode": "read-only" if FakeCodex.safe_settings else "workspace-write",
            "approval_policy": "never" if FakeCodex.safe_settings else "auto_review",
            "web_search": "disabled" if FakeCodex.safe_settings else "enabled",
            "skills": {"include_instructions": not FakeCodex.safe_settings, "bundled": {"enabled": False}},
            "history": {"persistence": "none"},
            "project_doc_max_bytes": 0,
        }
        self._client = SimpleNamespace(request=lambda *args, **kwargs: SimpleNamespace(model_dump=lambda **kw: {
            "config": settings
        }))
        FakeCodex.configs.append(config)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self.closed = True

    def thread_start(self, **kwargs):
        thread = FakeThread(FakeCodex.handles.pop(0), kwargs["cwd"])
        FakeCodex.threads.append(thread)
        return thread

    def account(self, **kwargs):
        return SimpleNamespace(model_dump=lambda **kw: {"account": {"type": "chatgpt"}})


class CodexProviderTests(unittest.TestCase):
    def setUp(self):
        FakeCodex.handles = []
        FakeCodex.configs = []
        FakeCodex.threads = []
        FakeCodex.instances = []
        FakeCodex.safe_settings = True

    def test_stream_yields_deltas_and_validated_result_with_context(self):
        payload = json.dumps(valid_payload(), ensure_ascii=False)
        handle = FakeHandle([
            Event("item/agentMessage/delta", {"delta": payload[:20]}),
            Event("item/agentMessage/delta", {"delta": payload[20:]}),
            Event("item/completed", {"item": {"type": "agentMessage", "phase": "final_answer", "text": payload}}),
            Event("turn/completed", {"turn": {"status": "completed"}}),
        ])
        FakeCodex.handles = [handle]
        with mock.patch.object(codex_provider.codex_auth, "status", return_value={"code": "connected"}), mock.patch.object(codex_provider, "Codex", FakeCodex):
            events = list(codex_provider.CodexProvider().stream("extract", context(), config={"timeout": 2}))
        self.assertEqual([event["type"] for event in events], ["status", "delta", "delta", "final"])
        result = events[-1]["result"]
        self.assertFalse(result["is_demo"])
        self.assertEqual(result["provider"], "codex")
        self.assertEqual(result["payload"], valid_payload())
        self.assertIn("我想减少仓促决定", FakeCodex.threads[-1].prompts[0])
        self.assertEqual(FakeCodex.threads[-1].turn_kwargs["effort"].value, "high")
        self.assertTrue(all(instance.closed for instance in FakeCodex.instances))

    def test_not_logged_in_is_rejected_before_sdk_creation(self):
        with mock.patch.object(codex_provider.codex_auth, "status", return_value={"code": "not_logged_in", "message": "尚未连接 ChatGPT 登录。"}), mock.patch.object(codex_provider, "Codex", FakeCodex):
            with self.assertRaises(ProviderError) as error:
                list(codex_provider.CodexProvider().stream("extract", context(), config={"timeout": 2}))
        self.assertEqual(error.exception.code, "not_logged_in")
        self.assertEqual(FakeCodex.instances, [])

    def test_effective_config_disables_inherited_mcp_and_sensitive_features(self):
        payload = json.dumps(valid_payload(), ensure_ascii=False)
        FakeCodex.handles = [FakeHandle([
            Event("item/completed", {"item": {"type": "agentMessage", "phase": "final_answer", "text": payload}}),
            Event("turn/completed", {"turn": {"status": "completed"}}),
        ])]
        with mock.patch.object(codex_provider.codex_auth, "status", return_value={"code": "connected"}), mock.patch.object(codex_provider, "Codex", FakeCodex):
            list(codex_provider.CodexProvider().stream("extract", context(), config={"timeout": 2}))
        overrides = "\n".join(FakeCodex.configs[-1].config_overrides)
        self.assertIn('mcp_servers={"inherited"={enabled=false}}', overrides)
        for name in codex_provider.DISABLED_FEATURES:
            self.assertIn(f"features.{name}=false", overrides)

    def test_tool_item_fails_closed_and_stops_runtime(self):
        handle = FakeHandle([
            Event("item/completed", {"item": {"type": "commandExecution", "command": "echo secret"}}),
        ])
        FakeCodex.handles = [handle]
        with mock.patch.object(codex_provider.codex_auth, "status", return_value={"code": "connected"}), mock.patch.object(codex_provider, "Codex", FakeCodex):
            with self.assertRaises(ProviderError) as error:
                list(codex_provider.CodexProvider().stream("extract", context(), config={"timeout": 2}))
        self.assertEqual(error.exception.code, "policy_blocked")
        self.assertTrue(all(instance.closed for instance in FakeCodex.instances))

    def test_timeout_closes_sdk(self):
        handle = FakeHandle([], delay=0.2)
        FakeCodex.handles = [handle]
        with mock.patch.object(codex_provider.codex_auth, "status", return_value={"code": "connected"}), mock.patch.object(codex_provider, "Codex", FakeCodex):
            with self.assertRaises(ProviderError) as error:
                list(codex_provider.CodexProvider().stream("extract", context(), config={"timeout": 0.03}))
        self.assertEqual(error.exception.code, "timeout")
        self.assertFalse(handle.interrupted)
        self.assertTrue(FakeCodex.configs)
        self.assertEqual(FakeCodex.threads[-1].turn_kwargs["effort"].value, "high")
        self.assertTrue(all(instance.closed for instance in FakeCodex.instances))

    def test_disconnect_closes_runtime_without_waiting_for_interrupt_rpc(self):
        handle = FakeHandle([Event("item/agentMessage/delta", {"delta": '{"principle":'})])
        handle.interrupt = mock.Mock(side_effect=lambda: time.sleep(0.5))
        FakeCodex.handles = [handle]
        with mock.patch.object(codex_provider.codex_auth, "status", return_value={"code": "connected"}), mock.patch.object(codex_provider, "Codex", FakeCodex):
            stream = codex_provider.CodexProvider().stream("extract", context(), config={"timeout": 2})
            self.assertEqual(next(stream)["type"], "status")
            self.assertEqual(next(stream)["type"], "delta")
            started = time.monotonic()
            stream.close()
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, 0.3)
        self.assertTrue(all(instance.closed for instance in FakeCodex.instances))
        handle.interrupt.assert_not_called()

    def test_timeout_does_not_wait_for_an_unanswered_interrupt(self):
        handle = FakeHandle([], delay=0.5)
        handle.interrupt = mock.Mock(side_effect=lambda: time.sleep(0.5))
        FakeCodex.handles = [handle]
        with mock.patch.object(codex_provider.codex_auth, "status", return_value={"code": "connected"}), mock.patch.object(codex_provider, "Codex", FakeCodex):
            started = time.monotonic()
            with self.assertRaises(ProviderError) as error:
                list(codex_provider.CodexProvider().stream("extract", context(), config={"timeout": 0.03}))
            elapsed = time.monotonic() - started
        self.assertEqual(error.exception.code, "timeout")
        self.assertLess(elapsed, 0.3)
        self.assertTrue(all(instance.closed for instance in FakeCodex.instances))
        handle.interrupt.assert_not_called()

    def test_setup_rpc_is_included_in_request_deadline(self):
        def slow_start(client, **kwargs):
            time.sleep(0.5)
            return FakeThread(FakeHandle([]), kwargs['cwd'])
        FakeCodex.handles = [FakeHandle([])]
        with mock.patch.object(codex_provider.codex_auth, "status", return_value={"code": "connected"}), mock.patch.object(codex_provider, "Codex", FakeCodex), mock.patch.object(FakeCodex, "thread_start", slow_start):
            started = time.monotonic()
            with self.assertRaises(ProviderError) as error:
                list(codex_provider.CodexProvider().stream("extract", context(), config={"timeout": 0.03}))
            elapsed = time.monotonic() - started
        self.assertEqual(error.exception.code, "timeout")
        self.assertLess(elapsed, 0.3)
        self.assertTrue(all(instance.closed for instance in FakeCodex.instances))

    def test_silent_reasoning_emits_heartbeat_for_disconnect_detection(self):
        handle = FakeHandle([], delay=0.1)
        with mock.patch.object(codex_provider, 'HEARTBEAT_SECONDS', 0.01):
            stream = codex_provider._stream_events(handle, 1.0)
            self.assertIsNone(next(stream))
            stream.close()
        self.assertFalse(handle.interrupted)

    def test_stuck_close_terminates_only_its_owned_process(self):
        stopped = threading.Event()
        process = SimpleNamespace(poll=lambda: None, kill=mock.Mock(side_effect=stopped.set), wait=mock.Mock())
        client = SimpleNamespace(_client=SimpleNamespace(_proc=process), close=lambda: stopped.wait(0.5))
        with mock.patch.object(codex_provider, 'CLOSE_TIMEOUT', 0.02):
            started = time.monotonic()
            codex_provider._close(client)
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, 0.3)
        process.kill.assert_called_once()

    def test_late_sdk_initialization_is_closed_after_timeout(self):
        closed = threading.Event()
        def late_client(config):
            time.sleep(0.05)
            return SimpleNamespace(close=closed.set)
        with mock.patch.object(codex_provider, 'Codex', late_client):
            with self.assertRaises(ProviderError) as error:
                codex_provider._open_sdk(None, 0.01)
        self.assertEqual(error.exception.code, 'timeout')
        self.assertTrue(closed.wait(0.3))

    def test_failed_turn_does_not_leak_sdk_error_details(self):
        handle = FakeHandle([
            Event("turn/completed", {"turn": {"status": "failed", "error": {"message": "token secret@example.com"}}}),
        ])
        FakeCodex.handles = [handle]
        with mock.patch.object(codex_provider.codex_auth, "status", return_value={"code": "connected"}), mock.patch.object(codex_provider, "Codex", FakeCodex):
            with self.assertRaises(ProviderError) as error:
                list(codex_provider.CodexProvider().stream("extract", context(), config={"timeout": 2}))
        self.assertNotIn("secret@example.com", str(error.exception))

    def test_turn_errors_map_quota_and_auth_codes(self):
        for error_info, expected in [
            ({"codexErrorInfo": "usageLimitExceeded"}, "quota_exhausted"),
            ({"codexErrorInfo": "rateLimitExceeded"}, "quota_exhausted"),
            ({"codexErrorInfo": "quota exceeded"}, "quota_exhausted"),
            ({"status": 401, "message": "unauthorized"}, "not_logged_in"),
        ]:
            handle = FakeHandle([Event("turn/completed", {"turn": {"status": "failed", "error": error_info}})])
            FakeCodex.handles = [handle]
            with mock.patch.object(codex_provider.codex_auth, "status", return_value={"code": "connected"}), mock.patch.object(codex_provider, "Codex", FakeCodex):
                with self.assertRaises(ProviderError) as error:
                    list(codex_provider.CodexProvider().stream("extract", context(), config={"timeout": 2}))
            self.assertEqual(error.exception.code, expected)

    def test_safety_verification_fails_closed_for_unverified_settings(self):
        FakeCodex.safe_settings = False
        payload = json.dumps(valid_payload(), ensure_ascii=False)
        FakeCodex.handles = [FakeHandle([Event("item/completed", {"item": {"type": "agentMessage", "phase": "final_answer", "text": payload}})])]
        with mock.patch.object(codex_provider.codex_auth, "status", return_value={"code": "connected"}), mock.patch.object(codex_provider, "Codex", FakeCodex):
            with self.assertRaises(ProviderError) as error:
                list(codex_provider.CodexProvider().stream("extract", context(), config={"timeout": 2}))
        self.assertEqual(error.exception.code, "policy_blocked")

    def test_auth_account_and_login_calls_have_bounded_timeouts(self):
        codex_auth._SDK_TIMEOUT_SECONDS = 0.02

        class SlowAccount:
            def __init__(self, config):
                self.closed = False

            def account(self, **kwargs):
                time.sleep(0.1)
                return SimpleNamespace(model_dump=lambda **kw: {"account": {"type": "chatgpt"}})

            def close(self):
                self.closed = True

        with mock.patch.object(codex_auth, "Codex", SlowAccount):
            started = time.monotonic()
            status = codex_auth.status(refresh=True)
        self.assertEqual(status["code"], "timeout")
        self.assertLess(time.monotonic() - started, 0.08)

        class SlowLogin:
            def __init__(self, config):
                pass

            def login_chatgpt(self):
                time.sleep(0.1)

            def close(self):
                pass

        with mock.patch.object(codex_auth, "status", return_value={"code": "not_logged_in", "provider": "codex", "label": "未登录", "message": "请登录", "model": None}), mock.patch.object(codex_auth, "Codex", SlowLogin):
            started = time.monotonic()
            result = codex_auth.start_login()
        self.assertEqual(result["code"], "timeout")
        self.assertLess(time.monotonic() - started, 0.08)

    def test_old_login_waiter_cannot_clear_new_pending_login(self):
        old_handle = SimpleNamespace(wait=lambda: time.sleep(0.03), cancel=mock.Mock())
        old_client = SimpleNamespace(close=mock.Mock())
        codex_auth._pending = {"handle": old_handle, "client": old_client, "auth_url": "https://auth.openai.com/old"}
        thread = threading.Thread(target=codex_auth._wait_for_login, args=(old_client, old_handle))
        thread.start()
        time.sleep(0.005)
        new_handle = SimpleNamespace(wait=lambda: None, cancel=mock.Mock())
        new_client = SimpleNamespace(close=mock.Mock())
        codex_auth._pending = {"handle": new_handle, "client": new_client, "auth_url": "https://auth.openai.com/new"}
        thread.join(0.2)
        self.assertIs(codex_auth._pending["handle"], new_handle)
        codex_auth._pending = None

    def test_wrong_thread_directory_is_rejected_before_any_turn(self):
        thread = SimpleNamespace(read=lambda **kwargs: SimpleNamespace(model_dump=lambda **kw: {"thread": {"cwd": "C:/unexpected", "ephemeral": True}}))
        with self.assertRaises(ProviderError) as error:
            codex_provider._verify_thread(thread, "C:/isolated")
        self.assertEqual(error.exception.code, "policy_blocked")

    def test_status_rejects_api_auth_and_sanitizes_errors(self):
        codex_auth.reset_for_tests()
        api_client = SimpleNamespace(account=lambda **kwargs: SimpleNamespace(model_dump=lambda **kw: {"account": {"type": "api"}}), close=lambda: None)
        with mock.patch.object(codex_auth, "Codex", return_value=api_client):
            status = codex_auth.status(refresh=True)
        self.assertEqual(status["code"], "policy_blocked")
        self.assertNotIn("auth.json", status["message"])

        codex_auth.reset_for_tests()
        with mock.patch.object(codex_auth, "Codex", side_effect=RuntimeError("token secret@example.com")):
            status = codex_auth.status(refresh=True)
        self.assertEqual(status["code"], "request_failed")
        self.assertNotIn("secret@example.com", status["message"])

    def test_login_manager_uses_official_url_and_cancel_does_not_logout(self):
        codex_auth.reset_for_tests()
        login_handle = SimpleNamespace(auth_url="https://auth.openai.com/oauth/authorize", wait=mock.Mock(side_effect=lambda: time.sleep(0.1)), cancel=mock.Mock())
        client = SimpleNamespace(login_chatgpt=lambda: login_handle, close=mock.Mock())
        with mock.patch.object(codex_auth, "status", return_value={"code": "not_logged_in", "provider": "codex", "label": "未登录", "message": "请登录", "model": None}), mock.patch.object(codex_auth, "Codex", return_value=client):
            pending = codex_auth.start_login()
            self.assertEqual(pending["code"], "pending")
            self.assertTrue(pending["auth_url"].startswith("https://auth.openai.com/"))
            codex_auth.cancel_login()
        login_handle.cancel.assert_called_once()


if __name__ == "__main__":
    unittest.main()
