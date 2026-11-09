import json
from datetime import UTC, datetime

from promo.config import Config, Profile
from promo.llm.ledger import is_provider_call
from promo.llm.usage import day_start, usage_today
from promo.plan import planned_usage

NOW = datetime(2026, 11, 8, 5, 0, tzinfo=UTC)  # Pacific is UTC-8 then: 2026-11-07 21:00


def ts(y, mo, d, h):
    return datetime(y, mo, d, h, tzinfo=UTC).timestamp()


def cfg():
    return Config(
        profiles={
            "gem": Profile(name="gem", provider="fake", reset_tz="America/Los_Angeles"),
            "grq": Profile(name="grq", provider="fake"),
        },
        stages={"bible": "gem", "propose": "gem", "montage": "gem", "coldview": "grq",
                "judge": "grq"},
    )  # fmt: skip


def write_log(path, recs):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in recs) + "\n")


def rec(profile, t, **kw):
    base = {"ts": t, "profile": profile, "in_tokens": 10, "out_tokens": 5, "cache_hit": False,
            "ok": True, "error": None}  # fmt: skip
    return {**base, **kw}


def test_day_start_uses_provider_timezone():
    assert day_start(NOW, "UTC") == datetime(2026, 11, 8, 0, 0, tzinfo=UTC)
    assert day_start(NOW, "America/Los_Angeles") == datetime(2026, 11, 7, 8, 0, tzinfo=UTC)


def test_usage_sums_across_shows_and_respects_reset(tmp_path):
    write_log(tmp_path / "a" / "run_log.jsonl", [
        rec("gem", ts(2026, 11, 7, 9)),   # inside the Pacific day, before UTC midnight
        rec("gem", ts(2026, 11, 7, 7)),   # before the Pacific day started
        rec("grq", ts(2026, 11, 7, 9)),   # yesterday for a UTC profile
        rec("grq", ts(2026, 11, 8, 1)),
    ])  # fmt: skip
    write_log(tmp_path / "b" / "run_log.jsonl", [
        rec("gem", ts(2026, 11, 8, 2), in_tokens=100, out_tokens=0),
        rec("grq", ts(2026, 11, 8, 3)),
    ])  # fmt: skip
    u = usage_today(cfg(), tmp_path, NOW)
    assert (u["gem"].calls, u["gem"].tokens) == (2, 15 + 100)
    assert (u["grq"].calls, u["grq"].tokens) == (2, 30)


def test_cache_hits_and_annotations_are_not_provider_calls(tmp_path):
    recs = [
        rec("gem", ts(2026, 11, 8, 2), cache_hit=True),
        rec("gem", ts(2026, 11, 8, 2), error="invalid output: a: bad", ok=False),
        rec("", ts(2026, 11, 8, 2), error="call failed: x", ok=False),
        rec("gem", ts(2026, 11, 8, 2), error="429", ok=False),  # a rejected real request
        rec("gem", ts(2026, 11, 8, 2)),
    ]
    assert [is_provider_call(r) for r in recs] == [False, False, False, True, True]
    write_log(tmp_path / "a" / "run_log.jsonl", recs)
    assert usage_today(cfg(), tmp_path, NOW)["gem"].calls == 2


def test_missing_outputs_dir_and_bad_lines(tmp_path):
    assert usage_today(cfg(), tmp_path / "nope", NOW) == {}
    p = tmp_path / "a" / "run_log.jsonl"
    p.parent.mkdir()
    p.write_text("not json\n")
    assert usage_today(cfg(), tmp_path, NOW) == {}


def test_planned_usage_scales_with_show(show):
    plan = planned_usage(show, cfg())
    assert set(plan) == {"bible", "propose", "montage", "coldview", "judge"}
    assert plan["bible"][0] == 1 and plan["judge"][0] == 96
    assert all(t > 0 for _, t in plan.values())


def test_failed_attempts_that_never_reached_the_provider_do_not_count():
    base = rec("gem", ts(2026, 11, 8, 2), ok=False)
    for err in ("connection_error", "service_unavailable", "internal_server_error"):
        assert not is_provider_call({**base, "error": err})
    for err in ("429", "rate_limit_exceeded", "daily_quota", "truncated"):
        assert is_provider_call({**base, "error": err})
