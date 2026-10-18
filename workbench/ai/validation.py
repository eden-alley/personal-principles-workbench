"""Strict, database-free validation for AI suggestions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class ValidationError(ValueError):
    """Raised when an AI payload is not an allowed suggestion shape."""


_TASKS = {"extract", "revise", "chat", "decision"}
_KINDS = {"value", "belief", "strategy"}
_MAX_STRING = 4000
_MAX_LIST = 12
_MAX_CITATIONS = 32


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValidationError(f"{label}必须是对象")
    return value


def _string(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{label}必须是字符串")
    if not allow_empty and not value.strip():
        raise ValidationError(f"{label}不能为空")
    if len(value) > _MAX_STRING:
        raise ValidationError(f"{label}过长")
    return value


def _string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        raise ValidationError(f"{label}必须是列表")
    if len(value) > _MAX_LIST:
        raise ValidationError(f"{label}超过数量上限")
    return [_string(item, f"{label}[{index}]") for index, item in enumerate(value)]


def _source_has_body(context: Mapping[str, Any]) -> bool:
    source = context.get("source")
    if not isinstance(source, Mapping):
        return False
    return any(isinstance(source.get(key), str) and source[key].strip() for key in ("text", "transcript"))


def _allowed_object(payload: Mapping[str, Any], allowed: set[str], label: str) -> None:
    unknown = set(payload) - allowed
    if unknown:
        names = ", ".join(sorted(str(item) for item in unknown))
        raise ValidationError(f"{label}包含未知字段：{names}")


def _references(payload: Any, context: Mapping[str, Any]) -> dict[str, list[int]]:
    citations = _mapping(payload, "citations")
    _allowed_object(citations, {"versions", "observations"}, "citations")
    result: dict[str, list[int]] = {}
    for key, context_key in (("versions", "versions"), ("observations", "observations")):
        values = citations.get(key)
        if not isinstance(values, list):
            raise ValidationError(f"citations.{key}必须是列表")
        if len(values) > _MAX_CITATIONS:
            raise ValidationError(f"citations.{key}超过数量上限")
        known = {
            item.get("id")
            for item in (context.get(context_key) or [])
            if isinstance(item, Mapping) and isinstance(item.get("id"), int) and not isinstance(item.get("id"), bool)
        }
        checked: list[int] = []
        for index, value in enumerate(values):
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValidationError(f"citations.{key}[{index}]必须是整数 ID")
            if value not in known:
                raise ValidationError(f"citations.{key}[{index}]不是本次上下文中的真实 ID")
            checked.append(value)
        result[key] = checked
    return result


def _principle(value: Any) -> None:
    principle = _mapping(value, "principle")
    required = {"title", "statement", "kind", "when_to_use", "conditions", "boundaries", "questions", "tags"}
    _allowed_object(principle, required, "principle")
    for field in ("title", "statement", "when_to_use"):
        _string(principle.get(field), f"principle.{field}")
    kind = principle.get("kind")
    if not isinstance(kind, str) or kind not in _KINDS:
        raise ValidationError("principle.kind必须是 value、belief 或 strategy")
    for field in ("conditions", "boundaries", "questions", "tags"):
        _string_list(principle.get(field), f"principle.{field}")


def validate_output(task: str, payload: Any, context: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and return an AI payload without coercing any values."""

    if task not in _TASKS:
        raise ValidationError("不支持的 AI 任务")
    context = _mapping(context, "context")
    payload = _mapping(payload, "payload")

    if task in {"extract", "revise"}:
        _allowed_object(payload, {"principle", "reason", "recommendation", "citations"}, "payload")
        if task == "extract" and not _source_has_body(context):
            raise ValidationError("没有可供提炼的来源正文")
        _principle(payload.get("principle"))
        _string(payload.get("reason"), "reason")
        _string(payload.get("recommendation"), "recommendation")
        citations = _references(payload.get("citations"), context)
    elif task == "chat":
        _allowed_object(payload, {"answer", "question", "options", "citations"}, "payload")
        _string(payload.get("answer"), "answer")
        _string(payload.get("question"), "question", allow_empty=True)
        options = payload.get("options")
        if not isinstance(options, list):
            raise ValidationError("options必须是列表")
        if len(options) > _MAX_LIST:
            raise ValidationError("options超过数量上限")
        for index, option in enumerate(options):
            option = _mapping(option, f"options[{index}]")
            _allowed_object(option, {"action", "tradeoff"}, f"options[{index}]")
            _string(option.get("action"), f"options[{index}].action")
            _string(option.get("tradeoff"), f"options[{index}].tradeoff")
        citations = _references(payload.get("citations"), context)
    else:
        _allowed_object(payload, {"situation", "choice", "reason", "expectation", "citations"}, "payload")
        for field in ("situation", "choice", "reason", "expectation"):
            _string(payload.get(field), field)
        citations = _references(payload.get("citations"), context)

    # Return a shallow copy so validation never edits a caller-owned payload.
    return dict(payload)


__all__ = ["ValidationError", "validate_output"]
