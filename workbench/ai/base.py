"""Provider contracts shared by optional AI backends."""

from __future__ import annotations

from typing import Any, Iterator, Protocol


class ProviderError(RuntimeError):
    def __init__(self, message: str, code: str = "request_failed") -> None:
        super().__init__(message)
        self.code = code


class AIProvider(Protocol):
    def stream(self, task: str, context: dict, *, config: dict | None = None) -> Iterator[dict[str, Any]]: ...

    def status(self, *, refresh: bool = False) -> dict[str, Any]: ...


__all__ = ["AIProvider", "ProviderError"]
