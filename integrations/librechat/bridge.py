"""Local, read-only OpenAI Chat Completions bridge for LibreChat.

The bridge is intentionally independent of Django and the workbench database.
It translates the current LibreChat branch into one ephemeral Codex turn.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import hmac
import json
import logging
import math
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Iterable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from workbench.ai.base import ProviderError
from workbench.ai import codex_auth, codex_provider

try:  # The SDK is optional for installations that only use the web app.
    from openai_codex import Codex, ImageInput, TextInput
except Exception:  # pragma: no cover - exercised when SDK is unavailable
    Codex = ImageInput = TextInput = None

try:  # Pillow is an explicit runtime dependency for bounded image inspection.
    from PIL import Image
except Exception:  # pragma: no cover - deployment error is reported clearly
    Image = None


LOGGER = logging.getLogger(__name__)
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8767
DEFAULT_MODEL = "gpt-6.1-sol"
DEFAULT_EFFORT = "medium"
DEFAULT_TIMEOUT = 120.0
PREP_TIMEOUT = 30.0
MAX_REQUEST_BYTES = 8 * 1024 * 1024
MAX_TEXT_CHARS = 200_000
MAX_MESSAGES = 100
MAX_IMAGES = 8
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_WARM_REQUESTS = 20
MAX_OUTPUT_CHARS = 512_000
ALLOWED_HOSTS = {"localhost", "127.0.0.1", "host.docker.internal"}
ALLOWED_ROLES = {"system", "developer", "user", "assistant"}
DATA_IMAGE = re.compile(r"^data:image/(png|jpeg|webp);base64,(.+)$", re.IGNORECASE | re.DOTALL)


class BridgeError(ProviderError):
    def __init__(self, message: str, code: str = "request_failed", status: int = 400):
        super().__init__(message, code)
        self.status = status


def _error_status(code: str) -> int:
    return {
        "unauthorized": 401,
        "policy_blocked": 403,
        "busy": 429,
        "quota_exhausted": 502,
        "not_logged_in": 401,
        "timeout": 504,
        "sdk_unavailable": 503,
    }.get(code, 400)


def _safe_error(error: BaseException) -> BridgeError:
    if isinstance(error, BridgeError):
        return error
    if isinstance(error, ProviderError):
        return BridgeError(str(error), error.code, _error_status(error.code))
    converted = codex_provider._provider_error(error)
    return BridgeError(str(converted), converted.code, _error_status(converted.code))


def _json_response(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _sse(payload: Any) -> bytes:
    return b"data: " + _json_response(payload) + b"\n\n"


def _host_name(value: str | None) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    try:
        return (urlsplit("//" + value.strip()).hostname or "").lower()
    except ValueError:
        return ""


def _extract_base64_image(value: str) -> tuple[str, bytes]:
    match = DATA_IMAGE.fullmatch(value)
    if not match:
        if value.startswith("data:"):
            raise BridgeError("图片必须是 PNG、JPEG 或 WebP 的 base64 data URL。", "invalid_image")
        raise BridgeError("拒绝远程图片 URL；请由 LibreChat 先转换为图片 data URL。", "remote_image")
    kind, encoded = match.groups()
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise BridgeError("图片 data URL 的 base64 内容无效。", "invalid_image") from None
    if len(raw) > MAX_IMAGE_BYTES:
        raise BridgeError("图片过大，已拒绝处理。", "image_too_large")
    if Image is None:
        raise BridgeError("图片校验组件不可用，已拒绝处理。", "sdk_unavailable", 503)
    try:
        with Image.open(BytesIO(raw)) as image:
            if image.format.lower() not in {"png", "jpeg", "webp"}:
                raise BridgeError("图片格式不受支持。", "invalid_image")
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise BridgeError("图片像素数过大，已拒绝处理。", "image_too_large")
            image.verify()
    except BridgeError:
        raise
    except Exception:
        raise BridgeError("图片内容无法通过校验。", "invalid_image") from None
    return kind.lower(), raw


def _text_input(text: str):
    if TextInput is None:
        raise BridgeError("Codex SDK 当前不可用。", "sdk_unavailable", 503)
    return TextInput(text)


def _image_input(url: str):
    if ImageInput is None:
        raise BridgeError("Codex SDK 当前不可用。", "sdk_unavailable", 503)
    return ImageInput(url)


def _message_inputs(messages: Any) -> list[Any]:
    if not isinstance(messages, list) or not messages:
        raise BridgeError("messages 必须是非空数组。", "invalid_request")
    if len(messages) > MAX_MESSAGES:
        raise BridgeError("消息数量超过上限。", "request_too_large")
    inputs: list[Any] = []
    text_chars = 0
    image_count = 0
    for message in messages:
        if not isinstance(message, dict):
            raise BridgeError("每条消息必须是对象。", "invalid_request")
        role = message.get("role")
        if role not in ALLOWED_ROLES:
            raise BridgeError("只支持 system、developer、user、assistant 消息。", "unsupported_message")
        if any(key in message for key in ("tool_calls", "function_call", "audio")):
            raise BridgeError("不支持工具调用或音频消息。", "unsupported_message")
        content = message.get("content")
        inputs.append(_text_input(f"[{role}]"))
        if isinstance(content, str):
            text_chars += len(content)
            if text_chars > MAX_TEXT_CHARS:
                raise BridgeError("文本总量超过上限，请缩短后重试。", "request_too_large")
            inputs.append(_text_input(content))
            continue
        if not isinstance(content, list) or not content:
            raise BridgeError("消息 content 必须是文字或内容片段数组。", "invalid_request")
        for part in content:
            if not isinstance(part, dict):
                raise BridgeError("消息内容片段必须是对象。", "invalid_request")
            kind = part.get("type")
            if kind == "text":
                text = part.get("text")
                if not isinstance(text, str):
                    raise BridgeError("文字片段必须包含字符串 text。", "invalid_request")
                text_chars += len(text)
                if text_chars > MAX_TEXT_CHARS:
                    raise BridgeError("文本总量超过上限，请缩短后重试。", "request_too_large")
                inputs.append(_text_input(text))
            elif kind in {"image_url", "image"}:
                image_url = part.get("image_url")
                if isinstance(image_url, dict):
                    image_url = image_url.get("url")
                if not isinstance(image_url, str):
                    raise BridgeError("图片片段必须包含 url。", "invalid_image")
                _extract_base64_image(image_url)
                image_count += 1
                if image_count > MAX_IMAGES:
                    raise BridgeError("图片数量超过上限。", "request_too_large")
                inputs.append(_image_input(image_url))
            else:
                raise BridgeError("只支持 text 和 PNG/JPEG/WebP image_url 内容片段。", "unsupported_message")
    return inputs


@dataclass
class _Request:
    request_id: str
    model: str
    effort: str
    timeout: float
    stream: bool
    inputs: list[Any]


class _Session:
    def __init__(self, owner: "LibreChatBridge", request: _Request, client: Any, handle: Any, deadline: float):
        self.owner = owner
        self.request = request
        self.client = client
        self.handle = handle
        self.deadline = deadline
        self.closed = False

    def iter_text(self) -> Iterable[tuple[str, str | None]]:
        yield from self.owner._iter_text(self)

    def release(self, success: bool) -> None:
        if self.closed:
            return
        self.closed = True
        self.owner._release(self, success)


class LibreChatBridge:
    def __init__(self, *, token: str | None = None, models: Iterable[str] | None = None):
        value = token if token is not None else os.environ.get("LIBRECHAT_BRIDGE_TOKEN", "")
        if not isinstance(value, str) or not value.strip():
            raise ValueError("必须配置非空 LIBRECHAT_BRIDGE_TOKEN。")
        self._token = value
        configured = list(models) if models is not None else [
            item.strip() for item in os.environ.get("LIBRECHAT_BRIDGE_MODELS", DEFAULT_MODEL).split(",") if item.strip()
        ]
        self.models = tuple(dict.fromkeys(configured or [DEFAULT_MODEL]))
        self._busy = threading.Lock()
        self._state_lock = threading.RLock()
        self._warm_client = None
        self._warm_directory: str | None = None
        self._warm_count = 0
        self._auth_cache: dict[str, Any] | None = None
        self._auth_cache_until = 0.0
        self._closed = False

    def _authorize(self, headers: Any) -> None:
        origin = headers.get("Origin")
        if origin:
            raise BridgeError("拒绝浏览器跨源直接调用。", "origin_blocked", 403)
        host = _host_name(headers.get("Host"))
        if host not in ALLOWED_HOSTS:
            raise BridgeError("请求 Host 不在本机允许范围内。", "host_blocked", 403)
        authorization = headers.get("Authorization", "")
        scheme, separator, candidate = authorization.partition(" ")
        if scheme.lower() != "bearer" or not separator or not hmac.compare_digest(candidate, self._token):
            raise BridgeError("Authorization Bearer 校验失败。", "unauthorized", 401)

    def _auth_status(self, *, refresh: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if not refresh and self._auth_cache is not None and now < self._auth_cache_until:
            return dict(self._auth_cache)
        result = codex_auth.status(refresh=True)
        with self._state_lock:
            self._auth_cache = dict(result)
            self._auth_cache_until = now + 60.0 if result.get("code") == "connected" else now
        return dict(result)

    def health(self) -> dict[str, Any]:
        with self._state_lock:
            warm = self._warm_client is not None and not self._closed
            status = dict(self._auth_cache or {"code": "unknown"})
        return {"status": "ok", "provider": "codex", "model": self.models[0], "connected": status.get("code") == "connected", "warm": warm}

    def models_response(self) -> dict[str, Any]:
        return {"object": "list", "data": [{"id": model, "object": "model", "owned_by": "codex"} for model in self.models]}

    def validate_request(self, body: Any) -> _Request:
        if not isinstance(body, dict):
            raise BridgeError("请求体必须是 JSON 对象。", "invalid_request")
        model = body.get("model", DEFAULT_MODEL)
        if not isinstance(model, str):
            raise BridgeError("请求模型必须是字符串。", "invalid_request")
        if model not in self.models:
            raise BridgeError("请求模型不在允许列表中。", "model_not_allowed")
        if any(key in body for key in ("tools", "tool_choice", "functions", "function_call")):
            raise BridgeError("不支持工具调用。", "unsupported_message")
        effort = body.get("reasoning_effort", DEFAULT_EFFORT)
        if effort not in {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}:
            raise BridgeError("推理强度配置无效。", "invalid_request")
        try:
            timeout = float(body.get("timeout", DEFAULT_TIMEOUT))
        except (TypeError, ValueError):
            raise BridgeError("超时时间配置无效。", "invalid_request") from None
        if not math.isfinite(timeout) or timeout <= 0 or timeout > 300:
            raise BridgeError("超时时间必须在 0 到 300 秒之间。", "invalid_request")
        stream = body.get("stream", False)
        if not isinstance(stream, bool):
            raise BridgeError("stream 必须是布尔值。", "invalid_request")
        return _Request(str(uuid.uuid4()), model, effort, timeout, stream, _message_inputs(body.get("messages")))

    def _ensure_warm(self, deadline: float) -> Any:
        with self._state_lock:
            if self._closed:
                raise BridgeError("桥接器已关闭。", "request_failed", 503)
            if self._warm_client is not None:
                return self._warm_client
        directory = tempfile.mkdtemp(prefix="librechat-codex-bridge-")
        discovery = client = None
        try:
            base = codex_provider._base_overrides()
            discovery = codex_provider._open_sdk(codex_provider._sdk_config(directory, base), min(codex_provider._remaining(deadline), PREP_TIMEOUT))
            overrides = codex_provider._bounded_call(
                lambda: codex_provider._disabled_mcp_overrides(discovery),
                min(codex_provider._remaining(deadline), PREP_TIMEOUT),
            )
            codex_provider._close(discovery)
            discovery = None
            client = codex_provider._open_sdk(codex_provider._sdk_config(directory, base + tuple(overrides)), min(codex_provider._remaining(deadline), PREP_TIMEOUT))
            codex_provider._bounded_call(lambda: codex_provider._verify_safety(client), min(codex_provider._remaining(deadline), PREP_TIMEOUT))
            with self._state_lock:
                if self._closed:
                    raise BridgeError("桥接器已关闭。", "request_failed", 503)
                self._warm_client = client
                self._warm_directory = directory
                self._warm_count = 0
            return client
        except Exception:
            codex_provider._close(discovery)
            codex_provider._close(client)
            shutil.rmtree(directory, ignore_errors=True)
            raise

    def open_request(self, request: _Request) -> _Session:
        if not self._busy.acquire(blocking=False):
            raise BridgeError("已有请求正在处理，请稍后重试。", "busy", 429)
        deadline = time.monotonic() + request.timeout
        client = None
        try:
            status = codex_provider._bounded_call(
                lambda: self._auth_status(refresh=False),
                min(codex_provider._remaining(deadline), PREP_TIMEOUT),
            )
            if status.get("code") != "connected":
                raise BridgeError(status.get("message", "尚未连接 ChatGPT 登录。"), status.get("code", "request_failed"), _error_status(status.get("code", "request_failed")))
            client = self._ensure_warm(deadline)
            effort_type = getattr(codex_provider, "ReasoningEffort", None)
            effort = effort_type(request.effort) if effort_type is not None else request.effort
            directory = self._warm_directory
            if directory is None:
                raise BridgeError("Codex 临时目录不可用。", "policy_blocked", 503)
            thread = codex_provider._bounded_call(
                lambda: client.thread_start(cwd=directory, model=request.model, sandbox=codex_provider.Sandbox.read_only, approval_mode=codex_provider.ApprovalMode.deny_all, ephemeral=True, base_instructions="只处理当前聊天文字和图片。不得使用工具、命令、网络或文件；只返回给用户的自然语言 Markdown。"),
                min(codex_provider._remaining(deadline), PREP_TIMEOUT),
            )
            codex_provider._bounded_call(lambda: codex_provider._verify_thread(thread, directory), min(codex_provider._remaining(deadline), PREP_TIMEOUT))
            handle = codex_provider._bounded_call(lambda: thread.turn(request.inputs, effort=effort), min(codex_provider._remaining(deadline), PREP_TIMEOUT))
            return _Session(self, request, client, handle, deadline)
        except Exception as error:
            if client is not None:
                self._discard_runtime(client)
            self._busy.release()
            if isinstance(error, BridgeError):
                raise
            converted = _safe_error(error)
            raise converted from None

    def _iter_text(self, session: _Session) -> Iterable[tuple[str, str | None]]:
        deadline = session.deadline
        chunks = []
        output_chars = 0
        completed = False
        try:
            for event in codex_provider._stream_events(session.handle, codex_provider._remaining(deadline)):
                if event is None:
                    yield "heartbeat", None
                    continue
                method = getattr(event, "method", "")
                payload = codex_provider._event_payload(event)
                if method.startswith("item/") and any(name in method.lower() for name in ("command", "tool", "file", "shell", "mcp")):
                    raise BridgeError("检测到未授权工具事件，已阻止分析。", "policy_blocked", 403)
                if method.startswith("item/") and isinstance(payload.get("item"), dict):
                    item_type = payload["item"].get("type")
                    if item_type not in {"userMessage", "agentMessage", "reasoning"}:
                        raise BridgeError("检测到未授权工具事件，已阻止分析。", "policy_blocked", 403)
                if method == "item/agentMessage/delta":
                    text = payload.get("delta")
                    if isinstance(text, str):
                        chunks.append(text)
                        output_chars += len(text)
                        if output_chars > MAX_OUTPUT_CHARS:
                            raise BridgeError("Codex 输出过大，已拒绝处理。", "output_too_large", 502)
                        yield "text", text
                elif method in {"turn/failed", "turn/interrupted", "turn/timeout"}:
                    if method == "turn/timeout":
                        raise BridgeError("Codex 请求超时。", "timeout", 504)
                    error = payload.get("error") or payload.get("turn", {}).get("error") or "Codex 分析未完成。"
                    raise _safe_error(codex_provider._provider_error(error))
                elif method == "turn/completed":
                    turn = payload.get("turn") or {}
                    if turn.get("status") != "completed" or turn.get("error"):
                        raise _safe_error(codex_provider._provider_error(turn.get("error") or "Codex 分析未完成。"))
                    completed = True
            if not completed:
                raise BridgeError("Codex 分析未完成。", "request_failed", 502)
        except Exception:
            raise

    def _release(self, session: _Session, success: bool) -> None:
        try:
            if not success:
                self._discard_runtime(session.client)
            else:
                with self._state_lock:
                    self._warm_count += 1
                    rotate = self._warm_count >= MAX_WARM_REQUESTS
                    if rotate:
                        client = self._warm_client
                        directory = self._warm_directory
                        self._warm_client = None
                        self._warm_directory = None
                        self._warm_count = 0
                    else:
                        client = directory = None
                codex_provider._close(client)
                if directory:
                    shutil.rmtree(directory, ignore_errors=True)
        finally:
            self._busy.release()

    def _discard_runtime(self, expected: Any = None) -> None:
        with self._state_lock:
            if expected is not None and self._warm_client is not expected:
                return
            client = self._warm_client
            directory = self._warm_directory
            self._warm_client = None
            self._warm_directory = None
            self._warm_count = 0
        codex_provider._close(client)
        if directory:
            shutil.rmtree(directory, ignore_errors=True)

    def run_nonstream(self, session: _Session) -> dict[str, Any]:
        chunks = []
        try:
            for kind, text in session.iter_text():
                if kind == "text" and text:
                    chunks.append(text)
            content = "".join(chunks)
            result = {"id": "chatcmpl-" + session.request.request_id, "object": "chat.completion", "created": int(time.time()), "model": session.request.model, "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}]}
            session.release(True)
            return result
        except Exception:
            session.release(False)
            raise

    def stream_to(self, session: _Session, write) -> None:
        chunks = []
        sent_role = False
        try:
            write(b": bridge-ready\n\n")
            for kind, text in session.iter_text():
                if kind == "heartbeat":
                    write(b": heartbeat\n\n")
                    continue
                if text is None:
                    continue
                chunks.append(text)
                delta = {"content": text}
                if not sent_role:
                    delta["role"] = "assistant"
                    sent_role = True
                write(_sse({"id": "chatcmpl-" + session.request.request_id, "object": "chat.completion.chunk", "created": int(time.time()), "model": session.request.model, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}))
            write(_sse({"id": "chatcmpl-" + session.request.request_id, "object": "chat.completion.chunk", "created": int(time.time()), "model": session.request.model, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}))
            write(b"data: [DONE]\n\n")
            session.release(True)
        except Exception as error:
            converted = _safe_error(error)
            try:
                write(_sse({"error": {"message": str(converted), "type": converted.code, "code": converted.code}}))
                write(b"data: [DONE]\n\n")
            except Exception:
                pass
            session.release(False)

    def close(self) -> None:
        with self._state_lock:
            self._closed = True
            client = self._warm_client
            directory = self._warm_directory
            self._warm_client = None
            self._warm_directory = None
        codex_provider._close(client)
        if directory:
            shutil.rmtree(directory, ignore_errors=True)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    bridge: LibreChatBridge

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_json(self, status: int, payload: Any) -> None:
        raw = _json_response(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)

    def _error(self, error: BaseException) -> None:
        converted = _safe_error(error)
        self._send_json(converted.status, {"error": {"message": str(converted), "type": converted.code, "code": converted.code}})

    def _authorized(self) -> bool:
        try:
            self.bridge._authorize(self.headers)
            return True
        except Exception as error:
            self._error(error)
            return False

    def do_GET(self) -> None:
        if not self._authorized():
            return
        try:
            if self.path == "/healthz":
                self._send_json(200, self.bridge.health())
            elif self.path == "/v1/models":
                self._send_json(200, self.bridge.models_response())
            else:
                self._send_json(404, {"error": {"message": "路径不存在。", "type": "not_found", "code": "not_found"}})
        except Exception as error:
            self._error(error)

    def do_POST(self) -> None:
        if not self._authorized():
            return
        if self.path != "/v1/chat/completions":
            self._send_json(404, {"error": {"message": "路径不存在。", "type": "not_found", "code": "not_found"}})
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            self._error(BridgeError("Content-Length 无效。", "invalid_request"))
            return
        if length < 0 or length > MAX_REQUEST_BYTES:
            self._error(BridgeError("请求体过大或缺少 Content-Length。", "request_too_large"))
            return
        session = None
        try:
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise BridgeError("请求体不是有效 JSON。", "invalid_request") from None
            request = self.bridge.validate_request(body)
            session = self.bridge.open_request(request)
            if request.stream:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                self.bridge.stream_to(session, self.wfile.write)
            else:
                self._send_json(200, self.bridge.run_nonstream(session))
        except Exception as error:
            self._error(error)
        finally:
            if session is not None and not getattr(session, "closed", False):
                try:
                    session.release(False)
                except Exception:
                    pass


def create_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, bridge: LibreChatBridge | None = None) -> ThreadingHTTPServer:
    selected = bridge or LibreChatBridge()

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

        def server_close(self):
            selected.close()
            super().server_close()

    handler = type("LibreChatBridgeHandler", (_Handler,), {"bridge": selected})
    return Server((host, int(port)), handler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local LibreChat Codex bridge")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", default=DEFAULT_PORT, type=int)
    args = parser.parse_args(argv)
    server = create_server(args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
