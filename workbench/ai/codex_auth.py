"""ChatGPT-authenticated Codex status and login lifecycle.

This module deliberately exposes only coarse status.  It never reads or emits
auth files, account identifiers, tokens, or SDK stderr.
"""

from __future__ import annotations

import tempfile
import threading
import time
import urllib.parse
from typing import Any

from .base import ProviderError

try:  # The optional SDK is allowed to be absent in the local app.
    from openai_codex import Codex, CodexConfig
except Exception:  # pragma: no cover - exercised by deployment environments
    Codex = None
    CodexConfig = None


_CACHE_SECONDS = 30.0
_LOGIN_TIMEOUT_SECONDS = 300.0
_SDK_TIMEOUT_SECONDS = 30.0
_MODEL = "gpt-6.1-sol"
_lock = threading.RLock()
_last_status: dict[str, Any] = {
    "provider": "codex",
    "code": "unknown",
    "label": "状态未知",
    "message": "尚未刷新 Codex 登录状态。",
    "model": _MODEL,
}
_last_refresh = 0.0
_pending: dict[str, Any] | None = None


def _status(code: str, message: str, *, model: str | None = _MODEL) -> dict[str, Any]:
    labels = {
        "not_logged_in": "未登录",
        "connected": "已连接",
        "quota_exhausted": "额度不足/限流",
        "request_failed": "请求失败",
        "timeout": "请求超时",
        "sdk_unavailable": "SDK 不可用",
        "policy_blocked": "策略阻止",
        "context_limit": "上下文超限",
        "unknown": "状态未知",
        "pending": "登录中",
        "invalid_output": "输出无效",
    }
    return {"provider": "codex", "code": code, "label": labels.get(code, labels["unknown"]), "message": message, "model": model}


def _set_status(value: dict[str, Any], *, refreshed: bool = True) -> dict[str, Any]:
    global _last_status, _last_refresh
    with _lock:
        _last_status = {key: value.get(key) for key in ("provider", "code", "label", "message", "model")}
        if refreshed:
            _last_refresh = time.monotonic()
        return dict(_last_status)


def reset_for_tests() -> None:
    global _last_status, _last_refresh, _pending
    with _lock:
        _pending = None
        _last_refresh = 0.0
        _last_status = _status("unknown", "尚未刷新 Codex 登录状态。")


def _config():
    if CodexConfig is None:
        return None
    return CodexConfig(
        cwd=tempfile.gettempdir(),
        config_overrides=(
            'history.persistence="none"',
            'project_doc_max_bytes=0',
            'sandbox_mode="read-only"',
            'approval_policy="never"',
            'skills.include_instructions=false',
            'skills.bundled.enabled=false',
            'features.skip_host_skill_discovery=true',
        ),
        env={"AI_API_KEY": "", "OPENAI_API_KEY": "", "CODEX_API_KEY": "", "CODEX_ACCESS_TOKEN": ""},
        client_name="principles_workbench_auth",
        client_title="Principles Workbench Codex Authentication",
    )


def _account_dump(client) -> dict[str, Any]:
    # Re-read official local login state; SDK inference handles token refresh when needed.
    # Forcing an OAuth refresh on every status poll adds unnecessary network failures.
    account = client.account(refresh_token=False)
    raw = account.model_dump(mode="json") if hasattr(account, "model_dump") else dict(account)
    value = raw.get("account", raw)
    return value if isinstance(value, dict) else {}


def _close(value) -> None:
    if value is None:
        return
    try:
        value.close()
    except Exception:
        try:
            value.__exit__(None, None, None)
        except Exception:
            pass


def _cancel_and_close(value) -> None:
    if value is None:
        return
    try:
        value.cancel()
    except Exception:
        pass
    _close(value)


def _bounded_call(function, timeout: float, cleanup=None):
    result: dict[str, Any] = {}
    finished = threading.Event()

    def worker():
        try:
            value = function()
            if result.get("abandoned"):
                if cleanup is not None:
                    cleanup(value)
                else:
                    _close(value)
            else:
                result["value"] = value
        except Exception as error:
            result["error"] = error
        finally:
            finished.set()

    threading.Thread(target=worker, daemon=True).start()
    if not finished.wait(timeout):
        result["abandoned"] = True
        raise TimeoutError
    if "error" in result:
        raise result["error"]
    return result.get("value")


def _classify_exception(error: BaseException) -> dict[str, Any]:
    text = str(error).lower()
    if isinstance(error, TimeoutError) or "timeout" in text or "timed out" in text:
        return _status("timeout", "Codex 请求超时。")
    if any(word in text for word in ("quota", "rate limit", "usage limit", "insufficient")):
        return _status("quota_exhausted", "Codex 额度暂不可用。")
    if any(word in text for word in ("login", "auth", "unauthorized", "not logged")):
        return _status("not_logged_in", "尚未连接 ChatGPT 登录。")
    return _status("request_failed", "无法读取 Codex 登录状态。")


def _refresh_status() -> dict[str, Any]:
    if Codex is None or CodexConfig is None:
        return _set_status(_status("sdk_unavailable", "Codex SDK 当前不可用。"))
    client = None
    try:
        client = _bounded_call(lambda: Codex(_config()), _SDK_TIMEOUT_SECONDS)
        account = _bounded_call(lambda: _account_dump(client), _SDK_TIMEOUT_SECONDS)
        account_type = account.get("type")
        if account_type == "chatgpt":
            return _set_status(_status("connected", "已连接 ChatGPT 登录，可使用 Codex 分析。"))
        if account_type == "api":
            return _set_status(_status("policy_blocked", "检测到 API 登录；为避免意外计费，仅接受 ChatGPT 登录。"))
        if not account:
            return _set_status(_status("not_logged_in", "尚未连接 ChatGPT 登录。"))
        return _set_status(_status("unknown", "无法确认 Codex 登录类型。"))
    except Exception as error:
        return _set_status(_classify_exception(error))
    finally:
        if client is not None:
            _close(client)


def status(*, refresh: bool = False) -> dict[str, Any]:
    with _lock:
        if _pending is not None:
            return _status("pending", "ChatGPT 登录流程等待完成。")
        if not refresh and _last_refresh and time.monotonic() - _last_refresh < _CACHE_SECONDS:
            return dict(_last_status)
        if not refresh and _last_refresh == 0:
            return dict(_last_status)
    return _refresh_status() if refresh else dict(_last_status)


def record_stream_error(code: str) -> None:
    messages = {
        "context_limit": "内容超过当前 Codex 模型上下文窗口。",
        "timeout": "Codex 请求超时。",
        "policy_blocked": "Codex 分析被安全策略阻止。",
        "quota_exhausted": "Codex 额度不足或受到限流。",
        "invalid_output": "Codex 返回内容未通过校验。",
        "not_logged_in": "尚未连接 ChatGPT 登录。",
        "request_failed": "Codex 请求失败。",
    }
    _set_status(_status(code if code in messages else "request_failed", messages.get(code, messages["request_failed"])), refreshed=False)


def _official_auth_url(url: Any) -> bool:
    if not isinstance(url, str):
        return False
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and host in {"auth.openai.com", "chatgpt.com", "auth.chatgpt.com"}


def _wait_for_login(client, handle) -> None:
    global _pending
    finished = threading.Event()
    outcome: dict[str, Any] = {}

    def wait_worker():
        try:
            handle.wait()
        except Exception as error:
            outcome["error"] = error
        finally:
            finished.set()

    try:
        threading.Thread(target=wait_worker, daemon=True).start()
        if not finished.wait(_LOGIN_TIMEOUT_SECONDS):
            try:
                handle.cancel()
            except Exception:
                pass
            _set_status(_status("timeout", "ChatGPT 登录等待超时。"))
            return
        if "error" in outcome:
            raise outcome["error"]
        with _lock:
            if _pending is None or _pending.get("handle") is not handle:
                return
        refreshed = _refresh_status()
        if refreshed["code"] == "connected":
            _set_status(refreshed)
    except Exception as error:
        _set_status(_classify_exception(error))
    finally:
        with _lock:
            owns_pending = _pending is not None and _pending.get("handle") is handle
            if owns_pending:
                _pending = None
        _close(client)


def start_login() -> dict[str, Any]:
    global _pending
    current = status(refresh=True)
    if current["code"] == "connected":
        return current
    if current["code"] == "policy_blocked":
        return current
    if Codex is None or CodexConfig is None:
        return _set_status(_status("sdk_unavailable", "Codex SDK 当前不可用。"))
    with _lock:
        if _pending is not None:
            return _status("pending", "ChatGPT 登录流程等待完成。") | {"auth_url": _pending.get("auth_url")}
    client = None
    try:
        client = _bounded_call(lambda: Codex(_config()), _SDK_TIMEOUT_SECONDS)
        def cleanup_login(value) -> None:
            _cancel_and_close(value)
            _close(client)

        handle = _bounded_call(client.login_chatgpt, _SDK_TIMEOUT_SECONDS, cleanup=cleanup_login)
        auth_url = getattr(handle, "auth_url", None)
        if not _official_auth_url(auth_url):
            try:
                handle.cancel()
            except Exception:
                pass
            raise ProviderError("登录地址不是官方 HTTPS 地址。", "policy_blocked")
        pending_status = _status("pending", "请在官方登录页面完成 ChatGPT 登录。")
        with _lock:
            _pending = {"client": client, "handle": handle, "auth_url": auth_url}
        _set_status(pending_status, refreshed=False)
        threading.Thread(target=_wait_for_login, args=(client, handle), daemon=True).start()
        return pending_status | {"auth_url": auth_url}
    except ProviderError as error:
        if client is not None:
            _close(client)
        return _set_status(_status(error.code, str(error)))
    except Exception as error:
        if client is not None:
            _close(client)
        return _set_status(_classify_exception(error))


def login_status() -> dict[str, Any]:
    current = status(refresh=False)
    with _lock:
        if _pending is not None:
            return current | {"auth_url": _pending.get("auth_url")}
    return current


def cancel_login() -> dict[str, Any]:
    global _pending
    with _lock:
        pending = _pending
        _pending = None
    if pending is not None:
        try:
            pending["handle"].cancel()
        except Exception:
            pass
        _close(pending["client"])
        return _set_status(_status("not_logged_in", "登录流程已取消，未退出 ChatGPT。"))
    return login_status()


__all__ = ["cancel_login", "login_status", "record_stream_error", "reset_for_tests", "start_login", "status"]
