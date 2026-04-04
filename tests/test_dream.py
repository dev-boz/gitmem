"""Tests for umx.dream — dream pipeline components."""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from umx.dream.conflict import (
    ConflictEntry,
    detect_conflicts,
    load_conflicts,
    write_conflicts_md,
)
from umx.dream.decay import apply_time_decay, decay_score, fact_age_days
from umx.dream.gates import DreamLock, read_dream_state, should_dream
from umx.dream.gitignore import GitignoreFilter
from umx.dream.notice import (
    clear_notice,
    read_notice,
    write_dream_log,
    write_notice,
)
from umx.models import DreamStatus, Fact, MemoryType, Scope, UmxConfig


def _make_fact(text: str = "test", **kwargs) -> Fact:
    defaults = dict(
        id=Fact.generate_id(),
        scope=Scope.PROJECT_TEAM,
        topic="test",
        encoding_strength=3,
        memory_type=MemoryType.EXPLICIT_EPISODIC,
        confidence=0.8,
        created=datetime.now(timezone.utc),
    )
    defaults.update(kwargs)
    return Fact(text=text, **defaults)


# ─── Gates ────────────────────────────────────────────────────


class TestDreamLock:
    def test_acquire_and_release(self, tmp_path: Path):
        lock = DreamLock(tmp_path)
        assert lock.acquire()
        assert lock.is_locked
        lock.release()
        assert not lock.is_locked

    def test_cannot_acquire_twice(self, tmp_path: Path):
        lock = DreamLock(tmp_path)
        assert lock.acquire()
        assert not lock.acquire()
        lock.release()

    def test_stale_lock_auto_released(self, tmp_path: Path):
        lock = DreamLock(tmp_path)
        # Write a stale lock (2 hours old)
        stale_time = datetime.now(timezone.utc) - timedelta(hours=2)
        lock.lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock.lock_path.write_text(json.dumps({
            "locked_at": stale_time.isoformat(),
            "pid": 99999,
        }))
        assert not lock.is_locked  # Stale lock should auto-clear


class TestShouldDream:
    def test_first_dream_always_triggers(self, tmp_path: Path):
        umx_dir = tmp_path / ".umx"
        umx_dir.mkdir()
        assert should_dream(umx_dir)

    def test_locked_never_triggers(self, tmp_path: Path):
        umx_dir = tmp_path / ".umx"
        umx_dir.mkdir()
        lock = DreamLock(umx_dir)
        lock.acquire()
        assert not should_dream(umx_dir)
        lock.release()

    def test_force_bypasses_gates(self, tmp_path: Path):
        umx_dir = tmp_path / ".umx"
        umx_dir.mkdir()
        # Write recent dream state
        (umx_dir / "MEMORY.md").write_text(
            f"last_dream: {datetime.now(timezone.utc).isoformat()}\n"
            f"session_count: 0\n"
        )
        assert should_dream(umx_dir, force=True)

    def test_force_still_respects_lock(self, tmp_path: Path):
        umx_dir = tmp_path / ".umx"
        umx_dir.mkdir()
        lock = DreamLock(umx_dir)
        lock.acquire()
        assert not should_dream(umx_dir, force=True)
        lock.release()

    def test_session_threshold_triggers(self, tmp_path: Path):
        umx_dir = tmp_path / ".umx"
        umx_dir.mkdir()
        now = datetime.now(timezone.utc)
        (umx_dir / "MEMORY.md").write_text(
            f"last_dream: {now.isoformat()}\n"
            f"session_count: 6\n"
        )
        assert should_dream(umx_dir, session_threshold=5)


# ─── Decay ────────────────────────────────────────────────────


class TestDecay:
    def test_decay_score_zero_age(self):
        assert decay_score(0) == pytest.approx(1.0)

    def test_decay_score_30_days(self):
        score = decay_score(30, decay_lambda=0.023)
        assert score == pytest.approx(0.50, abs=0.05)

    def test_decay_score_increases_with_lambda(self):
        fast = decay_score(15, decay_lambda=0.046)
        slow = decay_score(15, decay_lambda=0.023)
        assert fast < slow

    def test_fact_age_days(self):
        now = datetime.now(timezone.utc)
        fact = _make_fact(created=now - timedelta(days=10))
        age = fact_age_days(fact, now=now)
        assert age == pytest.approx(10.0, abs=0.01)

    def test_apply_time_decay_degrades_weak_stale(self):
        now = datetime.now(timezone.utc)
        fact = _make_fact(
            encoding_strength=2,
            corroborated_by=[],
            created=now - timedelta(days=200),
        )
        result = apply_time_decay([fact], now=now)
        assert result[0].encoding_strength < 2

    def test_apply_time_decay_preserves_strong(self):
        now = datetime.now(timezone.utc)
        fact = _make_fact(
            encoding_strength=4,
            created=now - timedelta(days=200),
        )
        result = apply_time_decay([fact], now=now)
        assert result[0].encoding_strength == 4


# ─── Conflict ─────────────────────────────────────────────────


class TestConflict:
    def test_detect_port_conflict(self):
        facts = [
            _make_fact("postgres runs on port 5433", topic="devenv", encoding_strength=4),
            _make_fact("postgres runs on port 5432", topic="devenv", encoding_strength=2),
        ]
        conflicts = detect_conflicts(facts)
        assert len(conflicts) >= 1

    def test_no_conflict_different_topics(self):
        facts = [
            _make_fact("port 5433", topic="database"),
            _make_fact("port 5432", topic="redis"),
        ]
        conflicts = detect_conflicts(facts)
        assert len(conflicts) == 0

    def test_write_and_load_conflicts(self, tmp_path: Path):
        fact_a = _make_fact("port 5433", topic="devenv", source_tool="claude-code")
        fact_b = _make_fact("port 5432", topic="devenv", source_tool="aider")
        conflict = ConflictEntry(
            topic="devenv",
            description="port conflict",
            fact_a=fact_a,
            fact_b=fact_b,
            resolution="Fact A wins",
        )
        write_conflicts_md(tmp_path, [conflict])

        loaded = load_conflicts(tmp_path)
        assert len(loaded) == 1
        assert loaded[0]["status"] == "OPEN"


# ─── Gitignore ────────────────────────────────────────────────


class TestGitignoreFilter:
    def test_basic_pattern(self):
        filt = GitignoreFilter(["*.env", "node_modules/"])
        assert filt.is_ignored(".env")
        assert filt.is_ignored("node_modules")
        assert not filt.is_ignored("src/main.py")

    def test_directory_pattern(self):
        filt = GitignoreFilter(["build/"])
        assert filt.is_ignored("build")
        assert filt.is_ignored("build/output.js")

    def test_negation(self):
        filt = GitignoreFilter(["*.log", "!important.log"])
        assert filt.is_ignored("debug.log")
        assert not filt.is_ignored("important.log")

    def test_from_file(self, tmp_path: Path):
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("*.pyc\n__pycache__/\n.env\n")
        filt = GitignoreFilter.from_file(gitignore)
        assert filt.is_ignored("test.pyc")
        assert filt.is_ignored("__pycache__")
        assert filt.is_ignored(".env")

    def test_sensitive_fact_detection(self):
        filt = GitignoreFilter([".env", "secrets.json"])
        assert filt.filter_sensitive_facts("set API_KEY=abc123")
        assert filt.filter_sensitive_facts("password is hunter2")
        assert not filt.filter_sensitive_facts("postgres runs on port 5433")


# ─── Notice ───────────────────────────────────────────────────


class TestNotice:
    def test_write_and_read(self, tmp_path: Path):
        write_notice(tmp_path, "test notice")
        result = read_notice(tmp_path)
        assert result is not None
        assert "test notice" in result

    def test_clear_notice(self, tmp_path: Path):
        write_notice(tmp_path, "test")
        clear_notice(tmp_path)
        assert read_notice(tmp_path) is None

    def test_read_nonexistent(self, tmp_path: Path):
        assert read_notice(tmp_path) is None

    def test_write_dream_log(self, tmp_path: Path):
        write_dream_log(
            tmp_path,
            DreamStatus.FULL,
            facts_added=5,
            facts_removed=2,
            provider="groq",
        )
        log = (tmp_path / "dream.log").read_text()
        assert "full" in log
        assert "Facts added: 5" in log
        assert "Facts removed: 2" in log
