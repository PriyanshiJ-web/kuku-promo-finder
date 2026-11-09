import json
import random

import pytest
from pydantic import BaseModel

from promo.config import Config, Profile
from promo.llm.cache import Cache
from promo.llm.client import LLM, MAX_ATTEMPTS
from promo.llm.ledger import FIELDS, BudgetExceeded, Ledger
from promo.llm.limiter import Limiter
from promo.llm.providers import FakeProvider, ProviderError, Usage, build_request


class Out(BaseModel):
    a: int


def make_cfg(**over) -> Config:
    prim = Profile(
        name="prim",
        provider="fake",
        model="m1",
        fallback=over.pop("fallback", None),
        price_in_per_m=1.0,
        price_out_per_m=2.0,
        **over,
    )
    fb = Profile(name="fb", provider="fake", model="m2")
    return Config(profiles={"prim": prim, "fb": fb}, stages={"s": "prim"})


class Rig:
    def __init__(self, tmp_path, script=None, fb_script=None, cfg=None, default=None):
        self.cfg = cfg or make_cfg()
        self.prompts = tmp_path / "prompts"
        self.prompts.mkdir(exist_ok=True)
        (self.prompts / "t.md").write_text("Say {{x}} as JSON.")
        self.log = tmp_path / "run_log.jsonl"
        self.prim = FakeProvider(script, default)
        self.fb = FakeProvider(fb_script)
        self.sleeps: list[float] = []

        async def fake_sleep(d):
            self.sleeps.append(d)

        self.llm = LLM(
            self.cfg, Ledger(self.log), Cache(tmp_path / "c.sqlite"),
            {"prim": self.prim, "fb": self.fb}, sleep=fake_sleep,
            rng=random.Random(1), prompts_dir=self.prompts,
        )  # fmt: skip

    async def call(self):
        return await self.llm.call_json("s", "t", {"x": "hi"}, Out, max_tokens=500)

    def lines(self):
        return [json.loads(x) for x in self.log.read_text().splitlines()]


async def test_cache_hit_second_call(tmp_path):
    r = Rig(tmp_path, default='{"a": 1}')
    assert (await r.call()).a == 1
    assert (await r.call()).a == 1
    assert len(r.prim.calls) == 1
    first, second = r.lines()
    assert first["cache_hit"] is False and first["cost_usd"] > 0
    assert second["cache_hit"] is True and second["cost_usd"] == 0
    assert second["in_tokens"] == 0 and second["out_tokens"] == 0


async def test_changed_template_is_cache_miss(tmp_path):
    r = Rig(tmp_path, default='{"a": 1}')
    await r.call()
    (r.prompts / "t.md").write_text("Say {{x}} as JSON, please.")
    await r.call()
    assert len(r.prim.calls) == 2


async def test_429_twice_then_success(tmp_path):
    err = ProviderError("rate", 429)
    r = Rig(tmp_path, script=[err, ProviderError("rate", 429, retry_after=7.0), '{"a": 2}'])
    assert (await r.call()).a == 2
    assert len(r.sleeps) == 2  # backoff was called
    assert r.sleeps[1] >= 7.0  # Retry-After honoured
    assert [x["ok"] for x in r.lines()] == [False, False, True]


async def test_five_failures_fall_back(tmp_path):
    cfg = make_cfg(fallback="fb")
    script = [ProviderError("down", 503)] * MAX_ATTEMPTS
    r = Rig(tmp_path, script=script, fb_script=['{"a": 9}'], cfg=cfg)
    assert (await r.call()).a == 9
    assert len(r.prim.calls) == MAX_ATTEMPTS and len(r.fb.calls) == 1
    assert r.lines()[-1]["profile"] == "fb"


async def test_non_retryable_error_fails_fast(tmp_path):
    r = Rig(tmp_path, script=[ProviderError("bad request", 400)])
    assert await r.call() is None
    assert len(r.prim.calls) == 1 and r.llm.failures


async def test_invalid_json_then_valid_is_repaired(tmp_path):
    r = Rig(tmp_path, script=["not json at all", '```json\n{"a": 3}\n```'])
    assert (await r.call()).a == 3
    assert len(r.prim.calls) == 2
    assert "failed validation" in r.prim.calls[1]["messages"][-1]["content"]


async def test_invalid_twice_returns_none_and_logs(tmp_path):
    r = Rig(tmp_path, script=['{"a": "x"}', '{"b": 1}'])
    assert await r.call() is None  # no exception
    assert len(r.llm.failures) == 1
    bad = [x for x in r.lines() if not x["ok"]]
    assert bad and "invalid output" in bad[-1]["error"]


async def test_failure_log_has_no_input_values(tmp_path):
    r = Rig(tmp_path, script=['{"a": "SECRET-TEXT"}', '{"a": "SECRET-TEXT"}'])
    await r.call()
    assert "SECRET-TEXT" not in r.log.read_text()
    assert "SECRET-TEXT" not in json.dumps(r.llm.failures)


def test_unsupported_params_not_sent():
    p = Profile(name="p", model="m", json_mode=False, supports_seed=False)
    req = build_request(p, [], json_mode=True, max_tokens=10, seed=0)
    assert "response_format" not in req and "seed" not in req
    q = p.model_copy(update={"json_mode": True, "supports_seed": True})
    req = build_request(q, [], json_mode=True, max_tokens=10, seed=0)
    assert req["response_format"] == {"type": "json_object"} and req["seed"] == 0


async def test_client_does_not_request_json_mode_when_profile_lacks_it(tmp_path):
    r = Rig(tmp_path, cfg=make_cfg(json_mode=False), default='{"a": 1}')
    await r.call()
    assert r.prim.calls[0]["json_mode"] is False and r.prim.calls[0]["seed"] is None


async def test_limiter_respects_rpm_with_fake_clock():
    now = [0.0]

    async def sleep(d):
        now[0] += d

    lim = Limiter(rpm=3, tpm=0, concurrency=1, clock=lambda: now[0], sleep=sleep)
    stamps = []
    for _ in range(10):
        await lim.acquire(10)
        stamps.append(now[0])
    for t in stamps:
        assert sum(1 for s in stamps if t <= s < t + 60) <= 3
    assert stamps[-1] >= 180  # 10 calls at 3 per minute


async def test_limiter_respects_tpm():
    now = [0.0]

    async def sleep(d):
        now[0] += d

    lim = Limiter(rpm=0, tpm=100, concurrency=1, clock=lambda: now[0], sleep=sleep)
    await lim.acquire(60)
    await lim.acquire(60)  # 120 > 100, must wait for the first to leave the window
    assert now[0] >= 60


async def test_ledger_one_line_per_call_with_all_fields(tmp_path):
    r = Rig(tmp_path, script=['{"a": 1}'])
    await r.call()
    lines = r.lines()
    assert len(lines) == 1
    assert set(lines[0]) == set(FIELDS)
    assert lines[0]["stage"] == "s" and lines[0]["model"] == "m1" and lines[0]["ok"] is True


def test_budget_guard(tmp_path):
    led = Ledger(None, max_calls=5, max_tokens=1000)
    led.log(in_tokens=400, out_tokens=100)
    led.check_budget(4, 100)
    with pytest.raises(BudgetExceeded, match="max_calls"):
        led.check_budget(5)
    with pytest.raises(BudgetExceeded, match="max_tokens"):
        led.check_budget(1, 600)


async def test_invalid_output_is_never_cached(tmp_path):
    r = Rig(tmp_path, script=["nope", "still nope", "nope", "still nope"])
    assert await r.call() is None
    assert await r.call() is None  # rerun must hit the provider again
    assert len(r.prim.calls) == 4
    assert r.llm.cache._db.execute("SELECT COUNT(*) FROM llm").fetchone()[0] == 0


async def test_repaired_output_is_cached_but_bad_first_reply_is_not(tmp_path):
    r = Rig(tmp_path, script=["nope", '{"a": 5}'])
    assert (await r.call()).a == 5
    assert r.llm.cache._db.execute("SELECT COUNT(*) FROM llm").fetchone()[0] == 1
    r.prim.script = ['{"a": 6}']
    assert (await r.call()).a == 6  # the bad first reply was not cached, so the provider is asked
    assert len(r.prim.calls) == 3


async def test_truncation_retries_with_doubled_max_tokens(tmp_path):
    trunc = ProviderError("cut", code="truncated")
    r = Rig(tmp_path, script=[trunc, '{"a": 1}'])
    out = await r.llm.call_json("s", "t", {"x": "hi"}, Out, max_tokens=100)
    assert out.a == 1
    assert [c["max_tokens"] for c in r.prim.calls] == [100, 200]
    assert len(r.prim.calls[1]["messages"]) == 1  # plain retry, not the JSON repair prompt
    assert r.lines()[0]["error"] == "truncated"
    assert r.sleeps == []  # not a backoff case


async def test_truncation_growth_is_capped_by_profile(tmp_path):
    trunc = ProviderError("cut", code="truncated")
    r = Rig(tmp_path, script=[trunc, '{"a": 1}'], cfg=make_cfg(max_output_tokens=150))
    await r.llm.call_json("s", "t", {"x": "hi"}, Out, max_tokens=100)
    assert [c["max_tokens"] for c in r.prim.calls] == [100, 150]


async def test_truncated_twice_fails_without_fallback(tmp_path):
    trunc = ProviderError("cut", code="truncated")
    r = Rig(tmp_path, script=[trunc, trunc], cfg=make_cfg(fallback="fb"))
    assert await r.llm.call_json("s", "t", {"x": "hi"}, Out, max_tokens=100) is None
    assert len(r.prim.calls) == 2 and len(r.fb.calls) == 0
    assert r.llm.failures


async def test_no_growth_possible_when_already_at_cap(tmp_path):
    r = Rig(
        tmp_path,
        script=[ProviderError("cut", code="truncated")],
        cfg=make_cfg(max_output_tokens=100),
    )
    assert await r.llm.call_json("s", "t", {"x": "hi"}, Out, max_tokens=100) is None
    assert len(r.prim.calls) == 1


def test_extra_body_passed_through_only_when_set():
    p = Profile(name="p", model="m")
    assert "extra_body" not in build_request(p, [], json_mode=False, max_tokens=5, seed=None)
    q = p.model_copy(update={"extra_body": {"reasoning_effort": "none"}})
    req = build_request(q, [], json_mode=False, max_tokens=5, seed=None)
    assert req["extra_body"] == {"reasoning_effort": "none"}


async def test_extra_body_is_part_of_cache_key(tmp_path):
    r = Rig(tmp_path, default='{"a": 1}')
    await r.call()
    await r.call()
    assert len(r.prim.calls) == 1
    r.cfg.profiles["prim"].extra_body = {"thinking": {"budget": 0}}
    await r.call()
    assert len(r.prim.calls) == 2


from promo.llm.providers import classify_error  # noqa: E402


def test_classify_groq_daily_vs_minute_limit():
    day = {
        "error": {
            "message": "Rate limit reached ... on tokens per day (TPD): Limit 1",
            "type": "tokens",
        }
    }
    minute = {
        "error": {
            "message": "Rate limit reached ... on tokens per minute (TPM)",
            "code": "rate_limit_exceeded",
        }
    }
    assert classify_error(429, day)[0] == "daily_quota"
    assert classify_error(429, minute)[0] == "rate_limit_exceeded"


def test_classify_gemini_perday_quota_id_in_details():
    body = [
        {
            "error": {
                "code": 429,
                "status": "RESOURCE_EXHAUSTED",
                "details": [
                    {
                        "violations": [
                            {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}
                        ]
                    }
                ],
            }
        }
    ]
    assert classify_error(429, body)[0] == "daily_quota"


def test_classify_json_validate_failed():
    body = {"error": {"message": "Failed to generate JSON", "code": "json_validate_failed"}}
    assert classify_error(400, body)[0] == "invalid_json"


def test_classify_retry_delay_header_wins_and_body_fallback():
    body = [{"error": {"details": [{"@type": "x/RetryInfo", "retryDelay": "37s"}]}}]
    assert classify_error(429, body, {})[1] == 37.0
    assert classify_error(429, body, {"retry-after": "5"})[1] == 5.0
    assert classify_error(429, {"error": {}}, {})[1] is None


def test_classify_never_exposes_body_text():
    body = {"error": {"message": "echo of SECRET model output " * 5, "code": "x" * 80}}
    code, _ = classify_error(500, body)
    assert code == "internal_server_error"  # long code rejected, status text used
    assert "SECRET" not in str(code)


async def test_daily_quota_exhausts_profile_and_skips_retries(tmp_path):
    quota = ProviderError("q", 429, code="daily_quota")
    cfg = make_cfg(fallback="fb")
    r = Rig(tmp_path, script=[quota], fb_script=['{"a": 1}', '{"a": 2}'], cfg=cfg)
    assert (await r.call()).a == 1
    assert len(r.prim.calls) == 1 and r.sleeps == []  # no retries
    assert "prim" in r.llm.exhausted
    r.llm.cache = None  # force real calls
    assert (await r.call()).a == 2
    assert len(r.prim.calls) == 1  # exhausted profile is never called again
    assert r.lines()[0]["error"] == "daily_quota"


async def test_429_without_hint_has_two_second_floor(tmp_path):
    r = Rig(tmp_path, script=[ProviderError("rate", 429), '{"a": 1}'])
    await r.call()
    assert r.sleeps[0] >= 2.0


async def test_invalid_json_code_repairs_with_json_mode_off(tmp_path):
    bad = ProviderError("bad", 400, code="invalid_json")
    r = Rig(tmp_path, script=[bad, '{"a": 4}'])
    assert (await r.call()).a == 4
    assert r.prim.calls[0]["json_mode"] is True and r.prim.calls[1]["json_mode"] is False
    assert "Return ONLY valid JSON" in r.prim.calls[1]["messages"][-1]["content"]
    assert r.sleeps == []


def make_chain_cfg(fallback) -> Config:
    ps = {
        n: Profile(name=n, provider="fake", model=f"m-{n}", fallback=fb)
        for n, fb in (("prim", fallback), ("f1", None), ("f2", None))
    }
    return Config(profiles=ps, stages={"s": "prim"})


def chain_rig(tmp_path, fallback, scripts):
    cfg = make_chain_cfg(fallback)
    r = Rig(tmp_path, cfg=cfg)
    r.prim = FakeProvider(scripts["prim"])
    f1, f2 = FakeProvider(scripts.get("f1")), FakeProvider(scripts.get("f2"))
    r.llm.providers = {"prim": r.prim, "f1": f1, "f2": f2}
    return r, f1, f2


async def test_fallback_list_is_tried_in_order(tmp_path):
    down = [ProviderError("down", 503)] * MAX_ATTEMPTS
    r, f1, f2 = chain_rig(tmp_path, ["f1", "f2"], {"prim": down, "f1": down, "f2": ['{"a": 7}']})
    assert (await r.call()).a == 7
    assert len(f1.calls) == MAX_ATTEMPTS and len(f2.calls) == 1


async def test_fallback_list_skips_exhausted_profiles(tmp_path):
    quota = ProviderError("q", 429, code="daily_quota")
    r, f1, f2 = chain_rig(
        tmp_path, ["f1", "f2"], {"prim": [quota], "f1": [quota], "f2": ['{"a": 8}', '{"a": 9}']}
    )
    assert (await r.call()).a == 8
    assert r.llm.exhausted == {"prim", "f1"}
    r.llm.cache = None
    assert (await r.call()).a == 9
    assert len(r.prim.calls) == 1 and len(f1.calls) == 1  # never called again


async def test_all_profiles_exhausted_returns_none(tmp_path):
    quota = ProviderError("q", 429, code="daily_quota")
    r, f1, f2 = chain_rig(tmp_path, "f1", {"prim": [quota], "f1": [quota]})
    assert await r.call() is None
    assert "daily_quota" in r.llm.failures[0]["error"]


def test_string_fallback_still_works():
    assert Profile(name="a", fallback="b").fallbacks == ["b"]
    assert Profile(name="a", fallback=["b", "c"]).fallbacks == ["b", "c"]
    assert Profile(name="a").fallbacks == []


async def test_limiter_settle_replaces_estimate():
    now = [0.0]

    async def sleep(d):
        now[0] += d

    lim = Limiter(rpm=0, tpm=100, concurrency=1, clock=lambda: now[0], sleep=sleep)
    h = await lim.acquire(90)  # estimate
    lim.settle(h, 10)  # actual usage was small
    await lim.acquire(90)  # fits now: 10 + 90 <= 100, no waiting
    assert now[0] == 0.0


async def test_client_settles_window_to_actual_tokens(tmp_path):
    r = Rig(tmp_path, script=[('{"a": 1}', Usage(12, 3))])
    await r.call()
    win = r.llm._limiters["prim"]._win
    assert [h.tokens for h in win] == [15]  # not the estimate (input + max_tokens)


async def test_rejected_call_settles_to_zero_tokens(tmp_path):
    r = Rig(tmp_path, script=[ProviderError("rate", 429), '{"a": 1}'])
    await r.call()
    win = r.llm._limiters["prim"]._win
    assert win[0].tokens == 0 and win[1].tokens > 0


def test_max_tokens_is_required():
    import inspect

    sig = inspect.signature(LLM.call_json)
    assert sig.parameters["max_tokens"].default is inspect.Parameter.empty
    assert (
        inspect.signature(LLM.complete).parameters["max_tokens"].default is inspect.Parameter.empty
    )


def cap_cfg(prim_kw=None, fb_kw=None) -> Config:
    prim = Profile(name="prim", provider="fake", model="m1", fallback=["fb"], **(prim_kw or {}))
    fb = Profile(name="fb", provider="fake", model="m2", **(fb_kw or {}))
    return Config(profiles={"prim": prim, "fb": fb}, stages={"s": "prim"})


async def test_too_small_primary_is_skipped_for_fallback(tmp_path):
    r = Rig(tmp_path, cfg=cap_cfg({"max_input_tokens": 2}), default='{"a": 1}')
    r.fb = FakeProvider(default='{"a": 2}')
    r.llm.providers["fb"] = r.fb
    assert (await r.call()).a == 2
    assert len(r.prim.calls) == 0 and len(r.fb.calls) == 1
    skipped = [x for x in r.lines() if x["error"] == "skipped: too_small"]
    assert len(skipped) == 1 and skipped[0]["profile"] == "prim"


async def test_tpm_smaller_than_request_is_skipped(tmp_path):
    # request needs about input + max_tokens(500) tokens, primary allows only 100 per minute
    r = Rig(tmp_path, cfg=cap_cfg({"tpm": 100}))
    r.fb = FakeProvider(default='{"a": 3}')
    r.llm.providers["fb"] = r.fb
    assert (await r.call()).a == 3
    assert len(r.prim.calls) == 0


async def test_fallback_too_small_is_skipped_too(tmp_path):
    cfg = cap_cfg(fb_kw={"max_input_tokens": 2})
    r = Rig(tmp_path, cfg=cfg, script=[ProviderError("down", 503)] * MAX_ATTEMPTS)
    r.fb = FakeProvider(default='{"a": 9}')
    r.llm.providers["fb"] = r.fb
    assert await r.call() is None
    assert len(r.fb.calls) == 0
    assert [x["error"] for x in r.lines()].count("skipped: too_small") == 1


async def test_nothing_fits_fails_cleanly_without_calls(tmp_path):
    cfg = cap_cfg({"max_input_tokens": 2}, {"max_input_tokens": 2})
    r = Rig(tmp_path, cfg=cfg)
    assert await r.call() is None
    assert len(r.prim.calls) == 0 and len(r.fb.calls) == 0
    assert "fit" in r.llm.failures[0]["error"]
    assert r.llm.ledger.calls == 0  # skips do not count as calls


def test_extract_json_tolerates_fences_and_trailing_text():
    from promo.llm.client import extract_json

    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! {"a": 1} Hope that helps {not json}') == {"a": 1}
    assert extract_json('{"a": 1}\n{"b": 2}') == {"a": 1}
    assert extract_json('[{"a": 1}] trailing') == [{"a": 1}]
    with pytest.raises(ValueError):
        extract_json("no json here")


async def test_last_profile_reports_who_served(tmp_path):
    cfg = make_cfg(fallback="fb")
    r = Rig(
        tmp_path,
        script=[ProviderError("down", 503)] * MAX_ATTEMPTS,
        fb_script=['{"a": 1}'],
        cfg=cfg,
    )
    await r.call()
    assert r.llm.last_profile == "fb"
