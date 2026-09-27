# test_feature_flag_env_wiring.py
# date created: 2026-09-26 19:11:47
# date modified: 2026-09-26 19:12:38
# tags:

"""Tests that the autonomous-pass feature flags are actually read from the environment.

These six keys sat in `.env` for a long time with nothing reading them. Five happened to
agree with the literal default in `evelyn_config.py`, so the group stayed invisible; the one
that disagreed (`MASTER_LIBRARIAN_ENABLED`) left an autonomous pass switched off for days
while the operator's config said it was on.

A literal that silently outranks configuration is exactly the "config constant that is never
executed" class AGENTS.md §11 exists to catch, so it is pinned here rather than trusted to
review.
"""

import importlib
import pathlib
import re

import pytest

import evelyn_config

FEATURE_FLAGS = [
    "CONSOLIDATION_ENABLED",
    "FACT_EXTRACTION_ENABLED",
    "PROFILE_EVOLUTION_ENABLED",
    "MASTER_LIBRARIAN_ENABLED",
    "AUTO_JOURNAL_ENABLED",
    "AMBIENT_REFLECTIONS_ENABLED",
]

REPO_ROOT = pathlib.Path(evelyn_config.BASE_DIR)


def _reload_with(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return importlib.reload(evelyn_config)


@pytest.fixture(autouse=True)
def _restore_module():
    """Reload from the real environment afterwards so later tests see the true config."""
    yield
    importlib.reload(evelyn_config)


@pytest.mark.parametrize("flag", FEATURE_FLAGS)
def test_env_true_is_honored(monkeypatch, flag):
    cfg = _reload_with(monkeypatch, **{flag: "true"})
    assert getattr(cfg, flag) is True


@pytest.mark.parametrize("flag", FEATURE_FLAGS)
def test_env_false_is_honored(monkeypatch, flag):
    cfg = _reload_with(monkeypatch, **{flag: "false"})
    assert getattr(cfg, flag) is False


@pytest.mark.parametrize("raw,expected", [
    ("true", True), ("True", True), ("TRUE", True), ("1", True),
    ("yes", True), ("on", True), (" true ", True), ('"true"', True),
    ("false", False), ("False", False), ("0", False),
    ("no", False), ("off", False),
])
def test_accepted_spellings(monkeypatch, raw, expected):
    cfg = _reload_with(monkeypatch, AUTO_JOURNAL_ENABLED=raw)
    assert cfg.AUTO_JOURNAL_ENABLED is expected


@pytest.mark.parametrize("raw", ["", "maybe", "2", "enabled", "null"])
def test_unparseable_value_falls_back_to_the_default(monkeypatch, raw):
    """A typo must not silently flip an autonomous pass on. MASTER_LIBRARIAN defaults off."""
    cfg = _reload_with(monkeypatch, MASTER_LIBRARIAN_ENABLED=raw)
    assert cfg.MASTER_LIBRARIAN_ENABLED is False


def test_master_librarian_stays_off_by_default(monkeypatch):
    """The pass rewrites vault notes unattended; absent configuration it must not run."""
    monkeypatch.delenv("MASTER_LIBRARIAN_ENABLED", raising=False)
    monkeypatch.setattr(evelyn_config, "_load_dotenv", lambda *_a, **_k: None)
    cfg = importlib.reload(evelyn_config)
    # The reload re-imports _load_dotenv, so read the literal default straight from source.
    src = (REPO_ROOT / "evelyn_config.py").read_text(encoding="utf-8")
    default = re.search(
        r'MASTER_LIBRARIAN_ENABLED = _env_flag\("MASTER_LIBRARIAN_ENABLED", (True|False)\)', src
    )
    assert default is not None, "MASTER_LIBRARIAN_ENABLED is no longer read from the environment"
    assert default.group(1) == "False"
    assert isinstance(cfg.MASTER_LIBRARIAN_ENABLED, bool)


@pytest.mark.parametrize("flag", FEATURE_FLAGS)
def test_every_flag_is_read_through_env_flag(flag):
    """Guard against a future edit replacing a read with a literal again."""
    src = (REPO_ROOT / "evelyn_config.py").read_text(encoding="utf-8")
    assert re.search(rf'^{flag} = _env_flag\("{flag}", (True|False)\)', src, re.M), (
        f"{flag} must be read via _env_flag so a value set in .env is honored"
    )


def test_env_example_documents_every_flag():
    """A key the operator can set must be discoverable without reading the source."""
    example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    missing = [f for f in FEATURE_FLAGS if not re.search(rf"^{f}=", example, re.M)]
    assert not missing, f"undocumented in .env.example: {missing}"


def test_no_env_example_flag_is_left_unread():
    """The original defect stated generally: a documented key that nothing consumes."""
    example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    src = (REPO_ROOT / "evelyn_config.py").read_text(encoding="utf-8")
    documented = set(re.findall(r"^([A-Z0-9_]+_ENABLED)=", example, re.M))
    unread = sorted(k for k in documented if f'"{k}"' not in src)
    assert not unread, f"documented in .env.example but read by nothing: {unread}"
