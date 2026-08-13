"""TASK-013 unit tests: missing-key path, unavailable-model path (mocked
failure -- no real API call), valid-key path, default-model path."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.config import Config, ConfigError, TESTED_DEFAULT_MODEL, load_config

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("OPENAI_API_KEY", "OPENAI_MODEL", "LOG_LEVEL"):
        monkeypatch.delenv(var, raising=False)


def _no_dotenv_file(tmp_path: Path) -> Path:
    # A path that doesn't exist, so load_dotenv finds nothing and tests
    # stay hermetic regardless of what's in the developer's shell/.env.
    return tmp_path / "does-not-exist.env"


def test_missing_key_starts_successfully_with_openai_available_false(tmp_path: Path) -> None:
    config = load_config(env_file=_no_dotenv_file(tmp_path))
    assert config.openai_available is False
    assert config.openai_api_key is None


def test_unavailable_model_override_fails_fast_with_specific_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
    monkeypatch.setenv("OPENAI_MODEL", "not-a-real-model")

    def failing_checker(model: str, api_key: str) -> None:
        raise ConfigError(f"OPENAI_MODEL={model!r} is unavailable")

    with pytest.raises(ConfigError, match="not-a-real-model"):
        load_config(env_file=_no_dotenv_file(tmp_path), model_checker=failing_checker)


def test_unavailable_model_override_never_falls_back_to_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
    monkeypatch.setenv("OPENAI_MODEL", "not-a-real-model")
    calls: list[str] = []

    def failing_checker(model: str, api_key: str) -> None:
        calls.append(model)
        raise ConfigError("boom")

    with pytest.raises(ConfigError):
        load_config(env_file=_no_dotenv_file(tmp_path), model_checker=failing_checker)
    assert calls == ["not-a-real-model"]  # never silently retried against the default


def test_valid_key_no_override_uses_tested_default_and_is_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
    config = load_config(env_file=_no_dotenv_file(tmp_path))
    assert config.openai_available is True
    assert config.openai_model == TESTED_DEFAULT_MODEL


def test_default_model_path_never_invokes_the_availability_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tested default is not re-verified against the live API on every
    startup (NFR-006) -- only an explicit override is."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
    calls: list[str] = []

    def spying_checker(model: str, api_key: str) -> None:
        calls.append(model)

    load_config(env_file=_no_dotenv_file(tmp_path), model_checker=spying_checker)
    assert calls == []


def test_valid_override_that_passes_the_check_is_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
    monkeypatch.setenv("OPENAI_MODEL", "a-real-model")
    config = load_config(env_file=_no_dotenv_file(tmp_path), model_checker=lambda m, k: None)
    assert config.openai_model == "a-real-model"
    assert config.openai_available is True


def test_env_example_contains_no_real_credential() -> None:
    content = (REPO_ROOT / ".env.example").read_text()
    for line in content.splitlines():
        if line.startswith("OPENAI_API_KEY="):
            assert line == "OPENAI_API_KEY="  # placeholder only, no value


def test_config_is_immutable() -> None:
    config = Config(openai_api_key=None, openai_model="x", openai_available=False, log_level="INFO")
    with pytest.raises(Exception):
        config.openai_model = "y"  # type: ignore[misc]
