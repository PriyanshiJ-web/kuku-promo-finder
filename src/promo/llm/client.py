"""LLM client: cache, limiter, retry, fallback profile and JSON repair (spec section 4)."""

from __future__ import annotations

import asyncio
import json
import random
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from promo.config import Config, ConfigError, Profile
from promo.llm.cache import Cache
from promo.llm.ledger import Ledger
from promo.llm.limiter import Limiter
from promo.llm.providers import OpenAIProvider, Provider, ProviderError
from promo.llm.templates import load_template
from promo.parse.render import est_tokens

T = TypeVar("T", bound=BaseModel)
MAX_ATTEMPTS = 5
CONNECTION_ATTEMPTS = 3  # timeouts and dropped connections: fail over to the fallback sooner
BACKOFF_BASE, BACKOFF_CAP = 1.0, 30.0


class LLMCallError(Exception):
    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


@dataclass
class Result:
    text: str
    in_tokens: int
    out_tokens: int
    cache_hit: bool
    profile: str
    model: str
    cache_key: str = ""


def extract_json(text: str):
    """Parse the first JSON value in a reply, tolerating code fences and surrounding prose."""
    t = text.strip()
    t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
    t = re.sub(r"\s*```$", "", t).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError as first:
        starts = [i for i in (t.find("{"), t.find("[")) if i != -1]
        if not starts:
            raise
        try:
            return json.JSONDecoder().raw_decode(t[min(starts) :])[0]
        except json.JSONDecodeError:
            raise first from None


def _summarize(e: ValueError) -> str:
    if isinstance(e, ValidationError):
        parts = [
            f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors(include_input=False)
        ]
        return "; ".join(parts)[:300]
    if isinstance(e, json.JSONDecodeError):
        return f"invalid JSON: {e.msg} at position {e.pos}"
    return str(e)[:300]


class LLM:
    def __init__(
        self,
        cfg: Config,
        ledger: Ledger,
        cache: Cache | None = None,
        providers: dict[str, Provider] | None = None,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
        prompts_dir: Path | None = None,
    ):
        self.cfg, self.ledger, self.cache = cfg, ledger, cache
        self.providers = dict(providers or {})
        self.sleep, self.clock = sleep, clock
        self.rng = rng or random.Random()
        self.prompts_dir = prompts_dir
        self._limiters: dict[str, Limiter] = {}
        self.exhausted: set[str] = set()  # profiles out of daily quota for this process
        self.failures: list[dict] = []
        self.last_profile: str = ""  # profile that served the most recent reply

    def _limiter(self, p: Profile) -> Limiter:
        if p.name not in self._limiters:
            self._limiters[p.name] = Limiter(p.rpm, p.tpm, p.concurrency, self.clock, self.sleep)
        return self._limiters[p.name]

    def _provider(self, p: Profile) -> Provider:
        if p.name not in self.providers:
            if p.provider == "fake":
                raise ConfigError(f"no FakeProvider registered for profile '{p.name}'")
            self.providers[p.name] = OpenAIProvider(p.resolved())
        return self.providers[p.name]

    async def complete(
        self,
        stage: str,
        messages: list[dict],
        *,
        prompt_name: str,
        max_tokens: int,
        template_sha: str = "",
        json_mode: bool = True,
        seed: int | None = 0,
    ) -> Result:
        """Primary with retries, then each fallback once, in order. Raises LLMCallError."""
        prof = self.cfg.profile_for(stage)
        chain = [prof]
        for name in prof.fallbacks:  # each fallback is tried once, in order, never chained further
            if name not in (p.name for p in chain):
                chain.append(self.cfg.profiles[name])
        last: Exception = LLMCallError("no profile")
        est_in = est_tokens("".join(m["content"] for m in messages))
        for p in chain:
            if p.max_input_tokens < est_in or p.tpm < est_in + max_tokens:
                self.ledger.log(
                    count=False, stage=stage, profile=p.name, model=p.model,
                    prompt_name=prompt_name, ok=False, error="skipped: too_small",
                )  # fmt: skip
                last = LLMCallError(
                    f"no profile can fit ~{est_in} input tokens plus {max_tokens} output",
                    "too_small",
                )
                continue
            try:
                return await self._attempts(
                    p, stage, messages, prompt_name, template_sha, max_tokens, json_mode, seed
                )
            except LLMCallError as e:
                if e.code in ("truncated", "invalid_json"):
                    raise  # output problems: another model would not fix them
                last = e
            except ConfigError as e:
                last = e
        raise LLMCallError(str(last), getattr(last, "code", None))

    async def _attempts(self, p, stage, messages, prompt_name, sha, max_tokens, json_mode, seed):
        if p.name in self.exhausted:
            raise LLMCallError(f"profile '{p.name}' is out of daily quota", "daily_quota")
        use_json = json_mode and p.json_mode
        mt = min(max_tokens, p.max_output_tokens)
        use_seed = seed if p.supports_seed else None
        params = {
            "json_mode": use_json, "max_tokens": mt, "seed": use_seed, "temperature": 0,
            "extra_body": p.extra_body,
        }  # fmt: skip
        base = {"stage": stage, "profile": p.name, "model": p.model, "prompt_name": prompt_name}
        key = Cache.make_key(f"{p.base_url}|{p.model}", sha, messages, params)
        if self.cache and (hit := self.cache.get(key)):
            self.ledger.log(cache_hit=True, **base)
            self.last_profile = p.name
            return Result(hit[0], hit[1], hit[2], True, p.name, p.model, key)
        provider = self._provider(p)
        limiter = self._limiter(p)
        est = est_tokens("".join(m["content"] for m in messages)) + mt  # settled after the call
        for attempt in range(1, MAX_ATTEMPTS + 1):
            t0 = self.clock()
            try:
                async with limiter.slot():
                    handle = await limiter.acquire(est)
                    text, usage = await provider.complete(
                        messages, json_mode=use_json, max_tokens=mt, seed=use_seed
                    )
            except ProviderError as e:
                if e.code != "truncated":
                    limiter.settle(handle, 0)  # a rejected call used no tokens
                err = e.code or f"{e.status or 'connection'}: {str(e)[:100]}"
                self.ledger.log(attempt=attempt, ok=False, error=err, **base)
                if e.code == "daily_quota":
                    self.exhausted.add(p.name)
                limit = CONNECTION_ATTEMPTS if e.status is None else MAX_ATTEMPTS
                if not e.retryable or attempt >= limit:
                    raise LLMCallError(f"profile '{p.name}' failed: {err}", e.code) from e
                cap = min(BACKOFF_CAP, BACKOFF_BASE * 2 ** (attempt - 1))
                delay = self.rng.uniform(0, cap)  # full jitter
                floor = (
                    e.retry_after if e.retry_after is not None else (2.0 if e.status == 429 else 0)
                )
                await self.sleep(max(delay, floor))
                continue
            limiter.settle(handle, usage.in_tokens + usage.out_tokens)
            cost = (usage.in_tokens * p.price_in_per_m + usage.out_tokens * p.price_out_per_m) / 1e6
            self.ledger.log(
                in_tokens=usage.in_tokens, out_tokens=usage.out_tokens, cost_usd=cost,
                latency_s=round(self.clock() - t0, 3), attempt=attempt, **base,
            )  # fmt: skip
            self.last_profile = p.name
            return Result(text, usage.in_tokens, usage.out_tokens, False, p.name, p.model, key)
        raise LLMCallError("unreachable")

    def _store(self, res: Result) -> None:
        if self.cache and not res.cache_hit:
            self.cache.put(res.cache_key, res.text, res.in_tokens, res.out_tokens)

    def _fail(self, stage: str, prompt_name: str, reason: str, profile: str = "") -> None:
        self.failures.append({"stage": stage, "prompt": prompt_name, "error": reason})
        self.ledger.log(
            count=False, stage=stage, profile=profile, prompt_name=prompt_name, ok=False,
            error=reason[:300],
        )  # fmt: skip

    async def _complete_growing(
        self,
        stage: str,
        messages: list[dict],
        name: str,
        sha: str,
        max_tokens: int,
        json_mode: bool = True,
    ) -> Result:
        """complete(), and on truncation one retry with max_tokens doubled (capped)."""
        try:
            return await self.complete(
                stage, messages, prompt_name=name, template_sha=sha, max_tokens=max_tokens,
                json_mode=json_mode,
            )  # fmt: skip
        except LLMCallError as e:
            cap = self.cfg.profile_for(stage).max_output_tokens
            base = max_tokens
            if e.code != "truncated" or min(base * 2, cap) <= base:
                raise
        return await self.complete(
            stage, messages, prompt_name=name, template_sha=sha, max_tokens=min(base * 2, cap),
            json_mode=json_mode,
        )  # fmt: skip

    async def call_json(
        self,
        stage: str,
        prompt_name: str,
        variables: dict,
        schema: type[T],
        *,
        max_tokens: int,
    ) -> T | None:
        """Render, call, validate, repair once, else record the failure and return None."""
        tpl = load_template(prompt_name, self.prompts_dir)
        messages = [{"role": "user", "content": tpl.render(variables)}]
        json_mode = True
        for repair in (False, True):
            name = f"{prompt_name}:repair" if repair else prompt_name
            try:
                res = await self._complete_growing(
                    stage, messages, name, tpl.sha, max_tokens, json_mode
                )
            except LLMCallError as e:
                if e.code == "invalid_json" and not repair:
                    # the provider rejected its own output (e.g. Groq json_validate_failed):
                    # repair with json mode off, no reply text is available to echo back
                    self.ledger.log(
                        count=False, stage=stage, prompt_name=name, ok=False, error="invalid_json"
                    )
                    last = messages[-1]
                    nudge = last["content"] + "\n\nReturn ONLY valid JSON."
                    messages = [*messages[:-1], {**last, "content": nudge}]
                    json_mode = False
                    continue
                self._fail(stage, prompt_name, f"call failed: {e}")
                return None
            try:
                out = schema.model_validate(extract_json(res.text))
                self._store(res)  # only validated output is cached
                return out
            except ValueError as e:
                err = _summarize(e)
                if repair:
                    self._fail(
                        stage, prompt_name, f"invalid output after repair: {err}", res.profile
                    )
                    return None
                self.ledger.log(
                    count=False, stage=stage, profile=res.profile, model=res.model,
                    prompt_name=name, ok=False, error=f"invalid output: {err}",
                )  # fmt: skip
                messages = [
                    *messages,
                    {"role": "assistant", "content": res.text},
                    {
                        "role": "user",
                        "content": f"Your reply failed validation: {err}\n"
                        "Fix it and return only the corrected JSON.",
                    },
                ]
        return None
