"""Small, replaceable AI provider with a conservative local demo mode."""

from __future__ import annotations

import json
import os
import socket
import ipaddress
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

from .validation import ValidationError, validate_output


from .base import ProviderError
from .prompts import _known_ids, _remote_payload


_DEFAULT_BASE_URL = "https://api.openai.com/v1"
_DEFAULT_MODEL = "gpt-4o-mini"
_DEFAULT_TIMEOUT = 30.0
_MAX_RESPONSE_BYTES = 256 * 1024
_MAX_REQUEST_BYTES = 512 * 1024


def _value(config: Mapping[str, Any], key: str, env_name: str, default: Any = None) -> Any:
    if key in config and config[key] is not None:
        return config[key]
    return os.environ.get(env_name, default)


def _text_excerpt(context: Mapping[str, Any]) -> str:
    source = context.get("source")
    if not isinstance(source, Mapping):
        return ""
    text = "\n".join(str(source.get(key, "")) for key in ("text", "transcript") if isinstance(source.get(key), str)).strip()
    if not text:
        return ""
    first = text.split("\n", 1)[0].strip()
    for mark in ("。", "！", "？", ".", "!", "?"):
        if mark in first:
            first = first.split(mark, 1)[0] + mark
            break
    return first[:800]


def _source_has_body(context: Mapping[str, Any]) -> bool:
    source = context.get("source")
    if not isinstance(source, Mapping):
        return False
    return any(isinstance(source.get(key), str) and source[key].strip() for key in ("text", "transcript"))


def _demo_payload(task: str, context: Mapping[str, Any]) -> dict[str, Any]:
    citations = {"versions": [], "observations": []}
    versions = context.get("versions") or []
    latest = versions[-1] if versions and isinstance(versions[-1], Mapping) else {}
    excerpt = _text_excerpt(context)
    if task == "extract":
        if not excerpt:
            raise ProviderError("没有可供提炼的来源正文")
        return {
            "principle": {
                "title": "待命名原则草稿",
                "statement": f"待校对素材：{excerpt}",
                "kind": "strategy",
                "when_to_use": "需要整理这段素材时",
                "conditions": ["素材已提供"],
                "boundaries": ["仍需人工确认适用边界"],
                "questions": ["这是否准确表达了你的意图？"],
                "tags": ["待校对"],
            },
            "reason": "演示草稿：只整理已提供素材，不自动认同其中价值或经历。",
            "recommendation": "请人工校对表述、类型、条件和边界，再决定是否保存。",
            "citations": citations,
        }
    if task == "revise":
        title = str(latest.get("title") or "待命名原则草稿")
        statement = str(latest.get("statement") or excerpt or "待补充原则表述")
        kind = latest.get("kind") if latest.get("kind") in {"value", "belief", "strategy"} else "strategy"
        citations["versions"] = _known_ids(context, "versions")[-1:]
        return {
            "principle": {
                "title": title,
                "statement": statement,
                "kind": kind,
                "when_to_use": "需要复核现有原则时",
                "conditions": ["已有一条原则草稿"],
                "boundaries": ["仍需人工确认适用边界"],
                "questions": ["这次修订解决了什么问题？"],
                "tags": ["待校对"],
            },
            "reason": "演示草稿：保留现有表述供你编辑，不虚构修订依据。",
            "recommendation": "请确认修改理由和边界后，再决定是否形成新版本。",
            "citations": citations,
        }
    if task == "chat":
        citations["versions"] = _known_ids(context, "versions")[-1:]
        citations["observations"] = _known_ids(context, "observations")[-1:]
        depth = str(context.get("depth") or "daily").strip().lower()
        title = str(latest.get("title") or "").strip()
        if title:
            answer = f"演示回答：找到适用原则“{title}”；这里只展示组织上下文的流程，不表示认同、验证或证伪。"
        else:
            answer = "演示回答：暂未找到适用原则；这里只展示如何组织当前描述，不把它当作已确认事实。"
        if depth in {"deep", "深入", "深度"}:
            question = "深入检查：这个判断依赖哪些前提？有哪些反例或与现有原则冲突的地方？"
            options = [
                {"action": "列出关键前提和反例", "tradeoff": "更稳妥，但需要更多思考时间。"},
                {"action": "对照适用原则的边界", "tradeoff": "有助于发现冲突，但可能暂时无法行动。"},
                {"action": "暂不选择，补充当前约束", "tradeoff": "保留选择空间，但结论会继续延后。"},
            ]
        else:
            question = "你现在最想先澄清的一个问题是什么？"
            options = [
                {"action": "说清当前想达成的结果", "tradeoff": "更容易聚焦，但需要先明确目标。"},
                {"action": "描述一个具体情境", "tradeoff": "更便于组织信息，但暂时不形成结论。"},
                {"action": "暂不选择，保留当前草稿", "tradeoff": "保留选择空间，但不会推进判断。"},
            ]
        return {
            "answer": answer,
            "question": question,
            "options": options,
            "citations": citations,
        }
    if task == "decision":
        citations["versions"] = _known_ids(context, "versions")[-1:]
        citations["observations"] = _known_ids(context, "observations")[-1:]
        situation = context.get("situation") if isinstance(context.get("situation"), str) else "当前情境"
        return {
            "situation": situation or "当前情境",
            "choice": "暂缓决定，先补充信息",
            "reason": "演示建议：当前上下文不足以替你确认选择。",
            "expectation": "补充事实后重新评估，再由你确认是否行动。",
            "citations": citations,
        }
    raise ProviderError("不支持的 AI 任务")


def _endpoint(base_url: str) -> str:
    if not isinstance(base_url, str) or not base_url.strip():
        raise ProviderError("AI 服务地址未配置")
    base_url = base_url.strip().rstrip("/")
    try:
        parsed = urllib.parse.urlsplit(base_url)
        hostname = parsed.hostname
    except ValueError:
        raise ProviderError("AI 服务地址格式无效") from None
    if parsed.scheme == "https":
        pass
    elif parsed.scheme == "http" and hostname:
        try:
            is_loopback = ipaddress.ip_address(hostname).is_loopback or hostname.lower() == "localhost"
        except ValueError:
            is_loopback = hostname.lower() == "localhost"
        if not is_loopback:
            raise ProviderError("AI 服务地址必须使用 HTTPS；HTTP 仅允许回环地址")
    else:
        raise ProviderError("AI 服务地址必须使用 HTTPS；HTTP 仅允许回环地址")
    return base_url if base_url.endswith("/chat/completions") else base_url + "/chat/completions"


def _call_remote(task: str, context: Mapping[str, Any], *, api_key: str, base_url: str, model: str, timeout: float, transport: Callable[..., Any] | None) -> dict[str, Any]:
    try:
        body = _remote_payload(task, context)
        body["model"] = model
        body["response_format"] = {"type": "json_object"}
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        endpoint = _endpoint(base_url)
        request = urllib.request.Request(endpoint, data=encoded, method="POST")
        request.add_header("Content-Type", "application/json")
        request.add_header("Authorization", f"Bearer {api_key}")
    except ProviderError:
        raise
    except Exception:
        raise ProviderError("AI 请求构造失败，请检查上下文和服务地址") from None
    if len(encoded) > _MAX_REQUEST_BYTES:
        raise ProviderError("发送内容过大，请缩减上下文")
    try:
        opener = transport or urllib.request.urlopen
        with opener(request, timeout=timeout) as response:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
    except (socket.timeout, TimeoutError):
        raise ProviderError("AI 请求超时") from None
    except urllib.error.HTTPError as error:
        raise ProviderError(f"AI 服务返回 HTTP 错误（{error.code}）") from None
    except urllib.error.URLError:
        raise ProviderError("网络请求失败，请检查 AI 服务地址") from None
    except OSError:
        raise ProviderError("网络请求失败，请稍后重试") from None
    except Exception:
        # A replaceable transport must not be able to leak implementation details
        # (or a secret embedded in an exception) through the public API.
        raise ProviderError("AI 请求失败，请稍后重试") from None
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise ProviderError("AI 响应过大，已拒绝处理")
    try:
        envelope = json.loads(raw.decode("utf-8"))
        content = envelope["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise TypeError
        payload = json.loads(content)
    except (ValueError, TypeError, KeyError, IndexError, RecursionError):
        raise ProviderError("AI 响应格式无效") from None
    if not isinstance(payload, dict):
        raise ProviderError("AI 响应格式无效")
    return payload


class DemoProvider:
    def stream(self, task, context, config=None):
        payload = validate_output(task, _demo_payload(task, context), context)
        yield {"type": "final", "result": {"payload": payload, "is_demo": True, "provider": "demo", "model": ""}}

    def status(self, refresh=False):
        return {"code": "demo", "label": "演示模式", "message": "本地示例，不调用模型服务。"}


class APIProvider:
    def stream(self, task, context, config=None):
        config = config or {}
        api_key = _value(config, "api_key", "AI_API_KEY", "")
        if not isinstance(api_key, str) or not api_key.strip():
            raise ProviderError("普通 API 尚未配置密钥，请配置本机 .env 或选择 Codex。", code="not_configured")
        model = _value(config, "model", "AI_MODEL", _DEFAULT_MODEL)
        if not isinstance(model, str) or not model.strip():
            raise ProviderError("AI 模型配置无效")
        try:
            timeout = float(_value(config, "timeout", "AI_TIMEOUT", _DEFAULT_TIMEOUT))
        except (TypeError, ValueError):
            raise ProviderError("AI 超时时间配置无效") from None
        if timeout <= 0 or timeout > 300:
            raise ProviderError("AI 超时时间必须在 0 到 300 秒之间")
        yield {"type": "status", "message": "正在等待普通 API 的完整结果…"}
        payload = _call_remote(task, context, api_key=api_key, model=model.strip(), timeout=timeout,
            base_url=_value(config, "base_url", "AI_BASE_URL", _DEFAULT_BASE_URL),
            transport=config.get("transport") if callable(config.get("transport")) else None)
        yield {"type": "final", "result": {"payload": payload, "is_demo": False, "provider": "api", "model": model.strip()}}

    def status(self, refresh=False):
        configured = bool(os.environ.get("AI_API_KEY", "").strip())
        return {"code": "configured" if configured else "not_configured", "label": "API 已配置（未验证）" if configured else "API 未配置", "message": "普通 API 使用独立服务商的密钥及计费。"}


_DEMO = DemoProvider()
_API = APIProvider()
_CODEX = None


def get_provider(name):
    global _CODEX
    if name == "demo":
        return _DEMO
    if name == "api":
        return _API
    if name == "codex":
        if _CODEX is None:
            from .codex_provider import CodexProvider
            _CODEX = CodexProvider()
        return _CODEX
    raise ProviderError("未知 AI 提供器，请在设置中重新选择。")


def stream_generate(task, context, *, config=None):
    if task not in {"extract", "revise", "chat", "decision"}:
        raise ProviderError("不支持的 AI 任务")
    if not isinstance(context, Mapping):
        raise ProviderError("AI 上下文必须是对象")
    if task == "extract" and not _source_has_body(context):
        raise ProviderError("没有可供提炼的来源正文，已拒绝模型调用")
    config = config or {}
    if not isinstance(config, Mapping):
        raise ProviderError("AI 配置必须是对象")
    name = config.get("provider") or os.environ.get("AI_PROVIDER", "").strip() or "codex"
    completed = False
    for event in get_provider(name).stream(task, context, config=config):
        if event.get("type") == "final":
            result = event.get("result", {})
            if type(result.get("is_demo")) is not bool:
                raise ProviderError("模型提供器返回了无效的模式标记。")
            try:
                result["payload"] = validate_output(task, result.get("payload"), context)
            except ValidationError as error:
                raise ProviderError(f"AI 输出未通过校验：{error}") from None
            result.setdefault("provider", name)
            result.setdefault("model", config.get("model", ""))
            completed = True
        yield event
    if not completed:
        raise ProviderError("模型没有返回完整结果，请重试。")


def generate(task, context, *, config=None):
    result = None
    for event in stream_generate(task, context, config=config):
        if event.get("type") == "final":
            result = event["result"]
    return result


__all__ = ["ProviderError", "generate", "stream_generate", "get_provider", "APIProvider", "DemoProvider"]
