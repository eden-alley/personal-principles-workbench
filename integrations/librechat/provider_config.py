"""Provider configuration and the small OpenAI-compatible HTTP adapter.

This module deliberately contains no Django or LibreChat state.  It is shared
by the foreground gateway and the background organizer so provider selection,
model routing, URL validation, and request headers cannot drift between them.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import math
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import urlsplit


DEFAULT_PROVIDER = "codex"
DEFAULT_CODEX_HOST = "127.0.0.1"
DEFAULT_CODEX_PORT = 8767
DEFAULT_API_TIMEOUT = 120.0
MAX_API_TIMEOUT = 300.0


class ProviderConfigError(ValueError):
    """An explicit, safe configuration error with no secret values."""

    code = "provider_config_invalid"


class ProviderRequestError(RuntimeError):
    """A provider response that cannot be used by the workbench."""

    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


def _env(env: Mapping[str, str] | None, name: str, default: str = "") -> str:
    value = (env if env is not None else __import__("os").environ).get(name, default)
    return value.strip() if isinstance(value, str) else ""


def _parse_timeout(value: str) -> float:
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        raise ProviderConfigError("LIBRARY_API_TIMEOUT 必须是有效数字。") from None
    if not math.isfinite(timeout) or timeout <= 0 or timeout > MAX_API_TIMEOUT:
        raise ProviderConfigError("LIBRARY_API_TIMEOUT 必须在 0 到 300 秒之间。")
    return timeout


def _is_loopback(host: str) -> bool:
    normalized = host.lower().strip("[]")
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _validated_base_url(value: str) -> tuple[str, str, int | None, str]:
    if not value:
        raise ProviderConfigError("API 模式缺少 LIBRARY_API_BASE_URL。")
    parsed = urlsplit(value)
    host = parsed.hostname
    if parsed.scheme not in {"https", "http"} or not host:
        raise ProviderConfigError("LIBRARY_API_BASE_URL 必须是 HTTPS 地址。")
    if parsed.scheme == "http" and not _is_loopback(host):
        raise ProviderConfigError("LIBRARY_API_BASE_URL 仅允许环回地址使用 HTTP。")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ProviderConfigError("LIBRARY_API_BASE_URL 不得包含凭据、查询参数或片段。")
    try:
        port = parsed.port
    except ValueError:
        raise ProviderConfigError("LIBRARY_API_BASE_URL 端口无效。") from None
    path = parsed.path.rstrip("/")
    return parsed.scheme, host, port, path


@dataclass(frozen=True)
class ProviderConfig:
    provider: str
    model: str
    timeout: float
    api_base_url: str = ""
    api_key: str = field(default="", repr=False)
    api_scheme: str = ""
    api_host: str = ""
    api_port: int | None = None
    api_path: str = ""
    codex_host: str = DEFAULT_CODEX_HOST
    codex_port: int = DEFAULT_CODEX_PORT

    @property
    def api_endpoint_path(self) -> str:
        path = self.api_path
        if path.endswith("/chat/completions"):
            return path
        return (path or "") + "/chat/completions"

    @property
    def api_headers(self) -> dict[str, str]:
        # Keep this the only place where the API credential enters an HTTP
        # request.  It is never part of the config repr or a frontend payload.
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    @staticmethod
    def codex_headers(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def api_payload(self, messages: Any, *, stream: bool = False) -> dict[str, Any]:
        # The ordinary API lane intentionally has a small allowlist.  In
        # particular, SDK-only reasoning fields and internal tenant headers do
        # not cross this provider boundary.
        return {"messages": messages, "model": self.model, "stream": stream}

    def api_connection(self) -> http.client.HTTPConnection:
        connection_type = http.client.HTTPSConnection if self.api_scheme == "https" else http.client.HTTPConnection
        return connection_type(self.api_host, self.api_port, timeout=self.timeout)


def load_provider_config(env: Mapping[str, str] | None = None) -> ProviderConfig:
    """Load and validate the provider selected for both LibreChat lanes."""

    provider = (_env(env, "LIBRARY_PROVIDER", DEFAULT_PROVIDER) or DEFAULT_PROVIDER).lower()
    if provider not in {"codex", "api"}:
        raise ProviderConfigError("LIBRARY_PROVIDER 只能是 codex 或 api。")

    if provider == "codex":
        return ProviderConfig(
            provider="codex",
            model="gpt-6.1-sol",
            timeout=DEFAULT_API_TIMEOUT,
        )

    key = _env(env, "LIBRARY_API_KEY")
    if not key:
        raise ProviderConfigError("API 模式缺少 LIBRARY_API_KEY。")
    model = _env(env, "LIBRARY_API_MODEL")
    if not model:
        raise ProviderConfigError("API 模式缺少 LIBRARY_API_MODEL。")
    base_url = _env(env, "LIBRARY_API_BASE_URL")
    scheme, host, port, path = _validated_base_url(base_url)
    return ProviderConfig(
        provider="api",
        model=model,
        timeout=_parse_timeout(_env(env, "LIBRARY_API_TIMEOUT", str(int(DEFAULT_API_TIMEOUT)))),
        api_base_url=base_url.rstrip("/"),
        api_key=key,
        api_scheme=scheme,
        api_host=host,
        api_port=port,
        api_path=path,
    )


def request_api_completion(config: ProviderConfig, messages: Any) -> dict[str, Any]:
    """Perform one non-streaming ordinary API call and return its JSON object."""

    if config.provider != "api":
        raise ProviderConfigError("当前 provider 不是 api。")
    connection = config.api_connection()
    try:
        connection.request("POST", config.api_endpoint_path, body=json.dumps(
            config.api_payload(messages), ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8"), headers=config.api_headers)
        response = connection.getresponse()
        raw = response.read()
        if response.status < 200 or response.status >= 300:
            raise ProviderRequestError("普通 API 暂不可用，请检查服务商配置。", status=response.status)
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ProviderRequestError("普通 API 返回格式无效。", status=response.status) from None
        if not isinstance(payload, dict):
            raise ProviderRequestError("普通 API 返回格式无效。", status=response.status)
        return payload
    except ProviderRequestError:
        raise
    except (OSError, TimeoutError):
        raise ProviderRequestError("普通 API 暂不可用，请检查服务商配置。") from None
    finally:
        connection.close()


__all__ = [
    "DEFAULT_API_TIMEOUT",
    "ProviderConfig",
    "ProviderConfigError",
    "ProviderRequestError",
    "load_provider_config",
    "request_api_completion",
]
