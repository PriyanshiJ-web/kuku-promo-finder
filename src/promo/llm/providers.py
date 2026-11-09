"""Provider protocol with an OpenAI-compatible implementation and a scripted fake."""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from http import HTTPStatus
from typing import Protocol

from promo.config import Profile


@dataclass
class Usage:
    in_tokens: int = 0
    out_tokens: int = 0


NON_RETRYABLE_CODES = {"truncated", "daily_quota", "invalid_json"}


class ProviderError(Exception):
    def __init__(
        self,
        message: str,
        status: int | None = None,
        retry_after: float | None = None,
        code: str | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after
        self.code = code

    @property
    def retryable(self) -> bool:
        if self.code in NON_RETRYABLE_CODES:
            return False
        return self.status is None or self.status in (408, 429) or self.status >= 500


class Provider(Protocol):
    async def complete(
        self, messages: list[dict], *, json_mode: bool, max_tokens: int, seed: int | None
    ) -> tuple[str, Usage]: ...


def _walk(o) -> Iterator[tuple[str, object]]:
    """Yield (key, value) pairs of a nested JSON-like body. Used only to inspect, never stored."""
    if isinstance(o, dict):
        for k, v in o.items():
            yield str(k), v
            yield from _walk(v)
    elif isinstance(o, list | tuple):
        for v in o:
            yield from _walk(v)


def classify_error(
    status: int | None, body: object = None, headers: Mapping | None = None
) -> tuple[str | None, float | None]:
    """Sanitized (code, retry_after) from an error response. The body is never kept or logged."""
    pairs = list(_walk(body))
    strings = [k for k, _ in pairs] + [v for _, v in pairs if isinstance(v, str)]
    blob = " ".join(strings).lower()
    code: str | None = None
    if status == 429 and (re.search(r"per ?day|\b(tpd|rpd)\b", blob)):
        code = "daily_quota"
    elif "json_validate_failed" in blob:
        code = "invalid_json"
    else:
        for key, val in pairs:
            if (
                key in ("code", "type")
                and isinstance(val, str)
                and re.fullmatch(r"[\w.\-]{1,40}", val)
            ):
                code = val.lower()
                break
    if code is None:
        try:
            code = HTTPStatus(status).phrase.lower().replace(" ", "_") if status else None
        except ValueError:
            code = None
        code = code or "connection_error"
    retry_after = None
    try:
        retry_after = float((headers or {}).get("retry-after", ""))
    except (ValueError, TypeError):
        for key, val in pairs:
            m = (
                re.fullmatch(r"([\d.]+)s", val)
                if key == "retryDelay" and isinstance(val, str)
                else None
            )
            if m:
                retry_after = float(m.group(1))
                break
    return code, retry_after


def build_request(
    profile: Profile, messages: list[dict], *, json_mode: bool, max_tokens: int, seed: int | None
) -> dict:
    """Only send parameters the profile supports."""
    req: dict = {
        "model": profile.model,
        "messages": messages,
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    if json_mode and profile.json_mode:
        req["response_format"] = {"type": "json_object"}
    if seed is not None and profile.supports_seed:
        req["seed"] = seed
    if profile.extra_body:
        req["extra_body"] = profile.extra_body
    return req


class OpenAIProvider:
    def __init__(self, profile: Profile):
        import openai

        self.profile = profile
        self._client = openai.AsyncOpenAI(
            base_url=profile.base_url, api_key=profile.api_key(), max_retries=0, timeout=240
        )

    async def complete(self, messages, *, json_mode, max_tokens, seed):
        import openai

        req = build_request(
            self.profile, messages, json_mode=json_mode, max_tokens=max_tokens, seed=seed
        )
        try:
            resp = await self._client.chat.completions.create(**req)
        except openai.APIStatusError as e:
            code, retry_after = classify_error(e.status_code, e.body, e.response.headers)
            raise ProviderError(type(e).__name__, e.status_code, retry_after, code) from e
        except openai.APIConnectionError as e:
            raise ProviderError(type(e).__name__, None, None, "connection_error") from e
        choice = resp.choices[0]
        if choice.finish_reason == "length":
            raise ProviderError("output cut off at max_tokens", code="truncated")
        text = choice.message.content or ""
        u = resp.usage
        if u is not None:
            return text, Usage(u.prompt_tokens or 0, u.completion_tokens or 0)
        chars = sum(len(m["content"]) for m in messages)
        return text, Usage(math.ceil(chars / 4), math.ceil(len(text) / 4))


class FakeProvider:
    """Scripted responses: str, (str, Usage), an Exception to raise, or callable(messages)."""

    def __init__(self, script: list | None = None, default: str | Callable | None = None):
        self.script = list(script or [])
        self.default = default
        self.calls: list[dict] = []

    async def complete(self, messages, *, json_mode, max_tokens, seed):
        self.calls.append(
            {"messages": messages, "json_mode": json_mode, "max_tokens": max_tokens, "seed": seed}
        )
        if self.script:
            item = self.script.pop(0)
        elif self.default is not None:
            item = self.default
        else:
            raise ProviderError("fake script exhausted", 500)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            item = item(messages)
        if isinstance(item, tuple):
            return item
        chars = sum(len(m["content"]) for m in messages)
        return item, Usage(math.ceil(chars / 4), math.ceil(len(item) / 4))
