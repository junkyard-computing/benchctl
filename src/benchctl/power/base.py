"""Power backend protocol + the HTTP seam shared by HTTP-driven backends."""

from __future__ import annotations

import urllib.request
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: str = ""


@runtime_checkable
class HttpClient(Protocol):
    def get(self, url: str, timeout: float | None = None) -> HttpResponse: ...


@runtime_checkable
class Power(Protocol):
    def off(self) -> None: ...
    def on(self) -> None: ...
    def cycle(self) -> None: ...
    def reachable(self) -> bool: ...


class UrllibHttpClient:
    """Real HTTP transport. Raises on connection failure (caller maps to unreachable)."""

    def get(self, url: str, timeout: float | None = None) -> HttpResponse:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            body = resp.read().decode("utf-8", "replace")
            return HttpResponse(resp.status, body)
