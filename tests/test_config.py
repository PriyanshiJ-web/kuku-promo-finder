import pytest

from promo.config import ConfigError, load_config, load_dotenv
from promo.llm.templates import load_template


def test_shipped_config_loads():
    cfg = load_config()
    assert {"gemini_flash", "gemini_lite", "groq_judge", "gemma", "custom", "fake"} <= set(
        cfg.profiles
    )
    assert cfg.stages["coldview"] == "gemini_lite" and cfg.stages["judge"] == "gemini_lite"
    assert cfg.params.max_len == 60 and cfg.params.max_tokens == 2_000_000


def test_no_placeholder_models_left():
    cfg = load_config()
    assert not [n for n, p in cfg.profiles.items() if p.model == "FILL_ME"]
    assert cfg.profiles["gemini_flash"].model == "gemini-3.8-flash"
    assert cfg.profiles["groq_judge"].model == "openai/gpt-oss-120b"


def test_profile_map_override():
    cfg = load_config(profile_map="propose=gemini_lite,judge=gemini_flash")
    assert cfg.stages["propose"] == "gemini_lite" and cfg.stages["judge"] == "gemini_flash"


def test_profile_map_errors():
    with pytest.raises(ConfigError, match="unknown stage"):
        load_config(profile_map="nope=gemini_lite")
    with pytest.raises(ConfigError, match="unknown profile"):
        load_config(profile_map="propose=nope")
    with pytest.raises(ConfigError, match="stage=profile"):
        load_config(profile_map="propose")


def test_fill_me_is_a_clear_error():
    cfg = load_config()
    cfg.profiles["gemini_flash"].model = "FILL_ME"
    with pytest.raises(ConfigError, match="FILL_ME"):
        cfg.check_ready(["propose"])


def test_missing_key_only_for_profiles_used(monkeypatch):
    cfg = load_config(profile_map="propose=fake,bible=fake,montage=fake,judge=fake")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="GEMINI_API_KEY"):
        cfg.check_ready(["coldview"])
    cfg.check_ready(["bible", "propose"])  # fake profile needs no key


def test_custom_profile_reads_env(monkeypatch):
    cfg = load_config()
    p = cfg.profiles["custom"]
    monkeypatch.delenv("CUSTOM_BASE_URL", raising=False)
    with pytest.raises(ConfigError, match="CUSTOM_BASE_URL"):
        p.resolved()
    monkeypatch.setenv("CUSTOM_BASE_URL", "http://x/v1")
    monkeypatch.setenv("CUSTOM_MODEL", "big-model")
    r = p.resolved()
    assert r.base_url == "http://x/v1" and r.model == "big-model"


def test_dotenv_does_not_override(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# c\nAAA_TEST=from_file\nBBB_TEST='quoted'\n")
    monkeypatch.setenv("AAA_TEST", "from_env")
    monkeypatch.delenv("BBB_TEST", raising=False)
    load_dotenv(env)
    import os

    assert os.environ["AAA_TEST"] == "from_env" and os.environ["BBB_TEST"] == "quoted"


def test_template_substitution_and_sha(tmp_path):
    (tmp_path / "a.md").write_text('Hello {{who}} {{data}} {"json": 1}')
    t = load_template("a", tmp_path)
    assert t.render({"who": "x", "data": {"k": [1]}}) == 'Hello x {"k": [1]} {"json": 1}'
    with pytest.raises(KeyError, match="missing variable"):
        t.render({"who": "x"})
    (tmp_path / "a.md").write_text("changed")
    assert load_template("a", tmp_path).sha != t.sha


def test_substituted_text_is_not_re_substituted(tmp_path):
    (tmp_path / "a.md").write_text("{{a}}")
    assert load_template("a", tmp_path).render({"a": "{{b}}"}) == "{{b}}"


def test_real_limits_and_fallback_chains():
    cfg = load_config()
    f = cfg.profiles["gemini_flash"]
    assert (f.rpm, f.tpm, f.rpd, f.tpd) == (5, 250000, 20, None)
    assert f.fallbacks == ["gemini_flash_b", "gemini_flash_c", "gemini_lite"]
    assert f.extra_body == {"reasoning_effort": "low"}
    assert cfg.profiles["gemini_lite"].fallbacks == ["gemini_lite_b"]
    assert (
        cfg.profiles["gemma"].json_mode is False and cfg.profiles["gemma"].max_input_tokens == 14000
    )
    g = cfg.profiles["groq_judge"]
    assert (g.tpd, g.max_input_tokens, g.fallbacks) == (200000, 6000, [])
    assert "groq_cold" not in cfg.profiles
    assert {cfg.stages[s] for s in ("bible", "propose", "montage")} == {"gemini_flash"}
