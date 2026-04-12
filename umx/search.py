from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from umx.config import UMXConfig, load_config
from umx.memory import iter_fact_files, load_all_facts, read_fact_file
from umx.models import fact_from_dict
from umx.scope import config_path
from umx.search_semantic import ensure_embeddings, rerank_candidates
from umx.sessions import iter_session_payloads, list_sessions, read_session


INDEX_NAME = "index.sqlite"
USAGE_NAME = "usage.sqlite"
REFERENCE_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "if",
    "in",
    "into",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "to",
    "was",
    "with",
}
TERM_RE = re.compile(r"[a-zA-Z0-9_]+")


def _utcnow_iso() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row["name"] for row in rows}


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = _table_columns(conn, table)
    for name, ddl in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def index_path(repo_dir: Path) -> Path:
    return repo_dir / "meta" / INDEX_NAME


def usage_path(repo_dir: Path) -> Path:
    return repo_dir / "meta" / USAGE_NAME


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def ensure_index(repo_dir: Path) -> None:
    conn = _connect(index_path(repo_dir))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS _meta (
          key TEXT PRIMARY KEY,
          value TEXT
        );
        CREATE TABLE IF NOT EXISTS memories (
          id TEXT PRIMARY KEY,
          repo TEXT,
          scope TEXT,
          topic TEXT,
          content TEXT,
          tags TEXT,
          encoding_strength INTEGER,
          verification TEXT,
          source_type TEXT,
          consolidation_status TEXT,
          task_status TEXT,
          token_count INTEGER,
          supersedes TEXT,
          superseded_by TEXT,
          created_at TEXT,
          git_sha TEXT,
          pr TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_memories_active ON memories(superseded_by) WHERE superseded_by IS NULL;
        CREATE INDEX IF NOT EXISTS idx_memories_topic ON memories(repo, topic);
        CREATE INDEX IF NOT EXISTS idx_memories_task ON memories(task_status) WHERE task_status IN ('open', 'blocked');
        CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
          content, tags,
          content='memories',
          content_rowid='rowid',
          tokenize='unicode61'
        );
        CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
          INSERT INTO memories_fts(rowid, content, tags) VALUES (new.rowid, new.content, new.tags);
        END;
        CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
          INSERT INTO memories_fts(memories_fts, rowid, content, tags)
          VALUES ('delete', old.rowid, old.content, old.tags);
        END;
        CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
          INSERT INTO memories_fts(memories_fts, rowid, content, tags)
          VALUES ('delete', old.rowid, old.content, old.tags);
          INSERT INTO memories_fts(rowid, content, tags) VALUES (new.rowid, new.content, new.tags);
        END;
        """
    )
    conn.commit()
    conn.close()


def ensure_usage_db(repo_dir: Path) -> None:
    conn = _connect(usage_path(repo_dir))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS usage (
          fact_id TEXT PRIMARY KEY,
          last_referenced TEXT,
          reference_count INTEGER DEFAULT 0,
          injected_count INTEGER DEFAULT 0,
          cited_count INTEGER DEFAULT 0,
          last_session TEXT,
          item_kind TEXT DEFAULT 'fact'
        );
        CREATE TABLE IF NOT EXISTS usage_events (
          event_id INTEGER PRIMARY KEY AUTOINCREMENT,
          fact_id TEXT NOT NULL,
          item_kind TEXT DEFAULT 'fact',
          session_id TEXT,
          turn_index INTEGER DEFAULT 0,
          event_kind TEXT NOT NULL,
          injection_point TEXT,
          disclosure_level TEXT,
          tool TEXT,
          parent_session_id TEXT,
          token_count INTEGER DEFAULT 0,
          session_tokens INTEGER DEFAULT 0,
          used_in_output INTEGER DEFAULT 0,
          content_preview TEXT,
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_usage_events_session ON usage_events(session_id, event_kind, event_id);
        CREATE INDEX IF NOT EXISTS idx_usage_events_fact ON usage_events(fact_id, event_kind, event_id);
        CREATE TABLE IF NOT EXISTS session_state (
          session_id TEXT PRIMARY KEY,
          parent_session_id TEXT,
          tool TEXT,
          turn_index INTEGER DEFAULT 0,
          estimated_tokens INTEGER DEFAULT 0,
          avg_tokens_per_turn INTEGER DEFAULT 250,
          context_window_tokens INTEGER DEFAULT 0,
          last_event_at TEXT,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS session_fact_state (
          session_id TEXT NOT NULL,
          fact_id TEXT NOT NULL,
          item_kind TEXT DEFAULT 'fact',
          last_injected_turn INTEGER DEFAULT 0,
          last_injected_tokens INTEGER DEFAULT 0,
          injection_count INTEGER DEFAULT 0,
          refresh_count INTEGER DEFAULT 0,
          last_injection_point TEXT,
          last_disclosure_level TEXT,
          last_tool TEXT,
          reference_count INTEGER DEFAULT 0,
          last_referenced_turn INTEGER,
          last_referenced_at TEXT,
          last_reference_preview TEXT,
          PRIMARY KEY (session_id, fact_id)
        );
        CREATE INDEX IF NOT EXISTS idx_session_fact_state_session ON session_fact_state(session_id, last_referenced_turn, last_referenced_at);
        """
    )
    _ensure_columns(
        conn,
        "usage",
        {
            "last_referenced": "TEXT",
            "reference_count": "INTEGER DEFAULT 0",
            "injected_count": "INTEGER DEFAULT 0",
            "cited_count": "INTEGER DEFAULT 0",
            "last_session": "TEXT",
            "item_kind": "TEXT DEFAULT 'fact'",
        },
    )
    _ensure_columns(
        conn,
        "session_state",
        {
            "parent_session_id": "TEXT",
            "tool": "TEXT",
            "turn_index": "INTEGER DEFAULT 0",
            "estimated_tokens": "INTEGER DEFAULT 0",
            "avg_tokens_per_turn": "INTEGER DEFAULT 250",
            "context_window_tokens": "INTEGER DEFAULT 0",
            "last_event_at": "TEXT",
            "created_at": "TEXT",
        },
    )
    conn.commit()
    conn.close()


def _insert_fact(conn: sqlite3.Connection, fact, repo_dir: Path) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO memories (
          id, repo, scope, topic, content, tags, encoding_strength, verification,
          source_type, consolidation_status, task_status, token_count, supersedes,
          superseded_by, created_at, git_sha, pr
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fact.fact_id,
            repo_dir.name,
            fact.scope.value,
            fact.topic,
            fact.text,
            json.dumps(fact.tags),
            fact.encoding_strength,
            fact.verification.value,
            fact.source_type.value,
            fact.consolidation_status.value,
            fact.task_status.value if fact.task_status else None,
            max(1, (len(fact.text) + 3) // 4),
            fact.supersedes,
            fact.superseded_by,
            fact.created.isoformat().replace("+00:00", "Z"),
            fact.code_anchor.git_sha if fact.code_anchor else None,
            fact.provenance.pr,
        ),
    )


def _compute_file_hashes(repo_dir: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in iter_fact_files(repo_dir):
        rel = str(path.relative_to(repo_dir))
        content = path.read_bytes()
        hashes[rel] = hashlib.md5(content).hexdigest()
    return hashes


def _store_file_hashes(conn: sqlite3.Connection, hashes: dict[str, str]) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO _meta(key, value) VALUES ('file_hashes', ?)",
        (json.dumps(hashes),),
    )


def _load_file_hashes(conn: sqlite3.Connection) -> dict[str, str] | None:
    row = conn.execute(
        "SELECT value FROM _meta WHERE key = 'file_hashes'"
    ).fetchone()
    if row is None:
        return None
    return json.loads(row["value"])


def rebuild_index(
    repo_dir: Path,
    *,
    with_embeddings: bool = False,
    config: UMXConfig | None = None,
) -> None:
    cfg = config or load_config(config_path())
    ensure_index(repo_dir)
    ensure_usage_db(repo_dir)
    conn = _connect(index_path(repo_dir))
    conn.execute("DELETE FROM memories")
    facts = load_all_facts(repo_dir, include_superseded=True)
    for fact in facts:
        _insert_fact(conn, fact, repo_dir)
    conn.execute(
        "INSERT OR REPLACE INTO _meta(key, value) VALUES ('schema_version', '2')"
    )
    _store_file_hashes(conn, _compute_file_hashes(repo_dir))
    conn.commit()
    conn.close()
    if with_embeddings:
        ensure_embeddings(
            repo_dir,
            [fact for fact in facts if fact.superseded_by is None],
            config=cfg,
            force=True,
        )


def query_index(
    repo_dir: Path,
    query: str,
    limit: int = 10,
    include_superseded: bool = False,
    *,
    config: UMXConfig | None = None,
):
    cfg = config or load_config(config_path())
    ensure_index(repo_dir)
    conn = _connect(index_path(repo_dir))
    clause = "" if include_superseded else "AND m.superseded_by IS NULL"
    candidate_limit = limit
    if cfg.search.backend == "hybrid":
        candidate_limit = max(limit, int(cfg.search.embedding.candidate_limit))
    sql = f"""
        SELECT m.*, bm25(memories_fts) AS rank
        FROM memories_fts
        JOIN memories m ON m.rowid = memories_fts.rowid
        WHERE memories_fts MATCH ?
        {clause}
        ORDER BY rank
        LIMIT ?
        """
    try:
        rows = conn.execute(sql, (query, candidate_limit)).fetchall()
    except sqlite3.OperationalError:
        rows = conn.execute(sql, (_fallback_match_query(query), candidate_limit)).fetchall()
    conn.close()
    facts = [fact_from_dict(
        {
            "fact_id": row["id"],
            "text": row["content"],
            "scope": row["scope"],
            "topic": row["topic"],
            "encoding_strength": row["encoding_strength"],
            "memory_type": "explicit_semantic",
            "verification": row["verification"],
            "source_type": row["source_type"],
            "consolidation_status": row["consolidation_status"],
            "task_status": row["task_status"],
            "provenance": {"pr": row["pr"], "extracted_by": "indexed", "sessions": []},
            "created": row["created_at"],
            "repo": row["repo"],
            "supersedes": row["supersedes"],
            "superseded_by": row["superseded_by"],
        }
    ) for row in rows]
    if cfg.search.backend != "hybrid" or not facts:
        return facts[:limit]
    ensure_embeddings(repo_dir, facts, config=cfg, force=False)
    facts_by_id = {fact.fact_id: fact for fact in facts}
    candidates = [
        (row["id"], -float(row["rank"]))
        for row in rows
    ]
    reranked = rerank_candidates(
        candidates,
        query=query,
        facts_by_id=facts_by_id,
        config=cfg,
        repo_dir=repo_dir,
    )
    ordered: list = []
    for fact_id, _ in reranked:
        fact = facts_by_id.get(fact_id)
        if fact is not None:
            ordered.append(fact)
        if len(ordered) >= limit:
            break
    return ordered


def _fallback_match_query(query: str) -> str:
    tokens = [token for token in re.split(r"\s+", query.strip()) if token]
    if not tokens:
        return '""'
    return " ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)


def session_snapshot(repo_dir: Path, session_id: str) -> dict[str, Any] | None:
    ensure_usage_db(repo_dir)
    conn = _connect(usage_path(repo_dir))
    row = conn.execute(
        "SELECT * FROM session_state WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def ensure_session_state(
    repo_dir: Path,
    session_id: str,
    *,
    tool: str | None = None,
    parent_session_id: str | None = None,
    avg_tokens_per_turn: int = 250,
    context_window_tokens: int | None = None,
) -> dict[str, Any]:
    ensure_usage_db(repo_dir)
    conn = _connect(usage_path(repo_dir))
    now = _utcnow_iso()
    row = conn.execute(
        "SELECT * FROM session_state WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    if row is None:
        snapshot = {
            "session_id": session_id,
            "parent_session_id": parent_session_id,
            "tool": tool,
            "turn_index": 0,
            "estimated_tokens": 0,
            "avg_tokens_per_turn": max(1, int(avg_tokens_per_turn)),
            "context_window_tokens": max(0, int(context_window_tokens or 0)),
            "last_event_at": now,
            "created_at": now,
        }
        conn.execute(
            """
            INSERT INTO session_state (
              session_id, parent_session_id, tool, turn_index, estimated_tokens,
              avg_tokens_per_turn, context_window_tokens, last_event_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot["session_id"],
                snapshot["parent_session_id"],
                snapshot["tool"],
                snapshot["turn_index"],
                snapshot["estimated_tokens"],
                snapshot["avg_tokens_per_turn"],
                snapshot["context_window_tokens"],
                snapshot["last_event_at"],
                snapshot["created_at"],
            ),
        )
    else:
        snapshot = {
            "session_id": session_id,
            "parent_session_id": parent_session_id or row["parent_session_id"],
            "tool": tool or row["tool"],
            "turn_index": int(row["turn_index"]),
            "estimated_tokens": int(row["estimated_tokens"]),
            "avg_tokens_per_turn": max(1, int(row["avg_tokens_per_turn"] or avg_tokens_per_turn)),
            "context_window_tokens": (
                max(0, int(context_window_tokens))
                if context_window_tokens is not None
                else int(row["context_window_tokens"] or 0)
            ),
            "last_event_at": now,
            "created_at": row["created_at"],
        }
        conn.execute(
            """
            UPDATE session_state
            SET parent_session_id = ?,
                tool = ?,
                avg_tokens_per_turn = ?,
                context_window_tokens = ?,
                last_event_at = ?
            WHERE session_id = ?
            """,
            (
                snapshot["parent_session_id"],
                snapshot["tool"],
                snapshot["avg_tokens_per_turn"],
                snapshot["context_window_tokens"],
                snapshot["last_event_at"],
                session_id,
            ),
        )
    conn.commit()
    conn.close()
    return snapshot


def advance_session_state(
    repo_dir: Path,
    session_id: str,
    *,
    tool: str | None = None,
    parent_session_id: str | None = None,
    observed_tokens: int | None = None,
    avg_tokens_per_turn: int = 250,
    context_window_tokens: int | None = None,
) -> dict[str, Any]:
    ensure_usage_db(repo_dir)
    conn = _connect(usage_path(repo_dir))
    now = _utcnow_iso()
    row = conn.execute(
        "SELECT * FROM session_state WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    if row is None:
        avg = max(1, int(avg_tokens_per_turn))
        delta = max(0, int(observed_tokens)) if observed_tokens is not None else avg
        snapshot = {
            "session_id": session_id,
            "parent_session_id": parent_session_id,
            "tool": tool,
            "turn_index": 1,
            "estimated_tokens": delta,
            "avg_tokens_per_turn": avg,
            "context_window_tokens": max(0, int(context_window_tokens or 0)),
            "last_event_at": now,
            "created_at": now,
        }
        conn.execute(
            """
            INSERT INTO session_state (
              session_id, parent_session_id, tool, turn_index, estimated_tokens,
              avg_tokens_per_turn, context_window_tokens, last_event_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot["session_id"],
                snapshot["parent_session_id"],
                snapshot["tool"],
                snapshot["turn_index"],
                snapshot["estimated_tokens"],
                snapshot["avg_tokens_per_turn"],
                snapshot["context_window_tokens"],
                snapshot["last_event_at"],
                snapshot["created_at"],
            ),
        )
    else:
        avg = max(1, int(row["avg_tokens_per_turn"] or avg_tokens_per_turn))
        delta = max(0, int(observed_tokens)) if observed_tokens is not None else avg
        snapshot = {
            "session_id": session_id,
            "parent_session_id": parent_session_id or row["parent_session_id"],
            "tool": tool or row["tool"],
            "turn_index": int(row["turn_index"]) + 1,
            "estimated_tokens": int(row["estimated_tokens"]) + delta,
            "avg_tokens_per_turn": avg,
            "context_window_tokens": (
                max(0, int(context_window_tokens))
                if context_window_tokens is not None
                else int(row["context_window_tokens"] or 0)
            ),
            "last_event_at": now,
            "created_at": row["created_at"],
        }
        conn.execute(
            """
            UPDATE session_state
            SET parent_session_id = ?,
                tool = ?,
                turn_index = ?,
                estimated_tokens = ?,
                avg_tokens_per_turn = ?,
                context_window_tokens = ?,
                last_event_at = ?
            WHERE session_id = ?
            """,
            (
                snapshot["parent_session_id"],
                snapshot["tool"],
                snapshot["turn_index"],
                snapshot["estimated_tokens"],
                snapshot["avg_tokens_per_turn"],
                snapshot["context_window_tokens"],
                snapshot["last_event_at"],
                session_id,
            ),
        )
    conn.commit()
    conn.close()
    return snapshot


def record_usage(
    repo_dir: Path,
    fact_id: str,
    *,
    injected: bool = False,
    cited: bool = False,
    session_id: str | None = None,
    referenced_at: str | None = None,
    item_kind: str = "fact",
) -> None:
    ensure_usage_db(repo_dir)
    conn = _connect(usage_path(repo_dir))
    conn.execute(
        """
        INSERT INTO usage (fact_id, last_referenced, reference_count, injected_count, cited_count, last_session, item_kind)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(fact_id) DO UPDATE SET
          last_referenced = CASE WHEN excluded.last_referenced IS NOT NULL THEN excluded.last_referenced ELSE usage.last_referenced END,
          reference_count = usage.reference_count + excluded.reference_count,
          injected_count = usage.injected_count + excluded.injected_count,
          cited_count = usage.cited_count + excluded.cited_count,
          last_session = COALESCE(excluded.last_session, usage.last_session),
          item_kind = COALESCE(excluded.item_kind, usage.item_kind)
        """,
        (
            fact_id,
            referenced_at if cited else None,
            1 if cited else 0,
            1 if injected else 0,
            1 if cited else 0,
            session_id,
            item_kind,
        ),
    )
    conn.commit()
    conn.close()


def record_injection(
    repo_dir: Path,
    fact_id: str,
    *,
    session_id: str | None = None,
    turn_index: int | None = None,
    session_tokens: int | None = None,
    injection_point: str = "prompt",
    disclosure_level: str = "l1",
    tool: str | None = None,
    parent_session_id: str | None = None,
    token_count: int = 0,
    item_kind: str = "fact",
) -> int | None:
    record_usage(repo_dir, fact_id, injected=True, session_id=session_id, item_kind=item_kind)
    ensure_usage_db(repo_dir)
    conn = _connect(usage_path(repo_dir))
    now = _utcnow_iso()
    cursor = conn.execute(
        """
        INSERT INTO usage_events (
          fact_id, item_kind, session_id, turn_index, event_kind, injection_point,
          disclosure_level, tool, parent_session_id, token_count, session_tokens,
          used_in_output, content_preview, created_at
        ) VALUES (?, ?, ?, ?, 'inject', ?, ?, ?, ?, ?, ?, 0, NULL, ?)
        """,
        (
            fact_id,
            item_kind,
            session_id,
            turn_index or 0,
            injection_point,
            disclosure_level,
            tool,
            parent_session_id,
            max(0, int(token_count)),
            max(0, int(session_tokens or 0)),
            now,
        ),
    )
    if session_id:
        refresh_delta = 1 if injection_point == "attention_refresh" else 0
        conn.execute(
            """
            INSERT INTO session_fact_state (
              session_id, fact_id, item_kind, last_injected_turn, last_injected_tokens,
              injection_count, refresh_count, last_injection_point, last_disclosure_level,
              last_tool
            ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
            ON CONFLICT(session_id, fact_id) DO UPDATE SET
              item_kind = excluded.item_kind,
              last_injected_turn = excluded.last_injected_turn,
              last_injected_tokens = excluded.last_injected_tokens,
              injection_count = session_fact_state.injection_count + 1,
              refresh_count = session_fact_state.refresh_count + ?,
              last_injection_point = excluded.last_injection_point,
              last_disclosure_level = excluded.last_disclosure_level,
              last_tool = excluded.last_tool
            """,
            (
                session_id,
                fact_id,
                item_kind,
                turn_index or 0,
                max(0, int(session_tokens or 0)),
                refresh_delta,
                injection_point,
                disclosure_level,
                tool,
                refresh_delta,
            ),
        )
    conn.commit()
    event_id = int(cursor.lastrowid) if cursor.lastrowid is not None else None
    conn.close()
    return event_id


def _mark_latest_injection_used(conn: sqlite3.Connection, session_id: str, fact_id: str) -> None:
    row = conn.execute(
        """
        SELECT event_id
        FROM usage_events
        WHERE session_id = ? AND fact_id = ? AND event_kind = 'inject'
        ORDER BY event_id DESC
        LIMIT 1
        """,
        (session_id, fact_id),
    ).fetchone()
    if row is None:
        return
    conn.execute(
        "UPDATE usage_events SET used_in_output = 1 WHERE event_id = ?",
        (row["event_id"],),
    )


def record_reference(
    repo_dir: Path,
    fact_id: str,
    *,
    session_id: str | None = None,
    turn_index: int | None = None,
    session_tokens: int | None = None,
    referenced_at: str | None = None,
    content_preview: str | None = None,
    item_kind: str = "fact",
) -> int | None:
    stamp = referenced_at or _utcnow_iso()
    record_usage(
        repo_dir,
        fact_id,
        cited=True,
        session_id=session_id,
        referenced_at=stamp,
        item_kind=item_kind,
    )
    ensure_usage_db(repo_dir)
    conn = _connect(usage_path(repo_dir))
    cursor = conn.execute(
        """
        INSERT INTO usage_events (
          fact_id, item_kind, session_id, turn_index, event_kind, injection_point,
          disclosure_level, tool, parent_session_id, token_count, session_tokens,
          used_in_output, content_preview, created_at
        ) VALUES (?, ?, ?, ?, 'reference', NULL, NULL, NULL, NULL, 0, ?, 1, ?, ?)
        """,
        (
            fact_id,
            item_kind,
            session_id,
            turn_index or 0,
            max(0, int(session_tokens or 0)),
            content_preview,
            stamp,
        ),
    )
    if session_id:
        conn.execute(
            """
            INSERT INTO session_fact_state (
              session_id, fact_id, item_kind, reference_count, last_referenced_turn,
              last_referenced_at, last_reference_preview
            ) VALUES (?, ?, ?, 1, ?, ?, ?)
            ON CONFLICT(session_id, fact_id) DO UPDATE SET
              item_kind = excluded.item_kind,
              reference_count = session_fact_state.reference_count + 1,
              last_referenced_turn = CASE
                WHEN excluded.last_referenced_turn > 0 THEN excluded.last_referenced_turn
                ELSE session_fact_state.last_referenced_turn
              END,
              last_referenced_at = excluded.last_referenced_at,
              last_reference_preview = excluded.last_reference_preview
            """,
            (
                session_id,
                fact_id,
                item_kind,
                turn_index or 0,
                stamp,
                content_preview,
            ),
        )
        _mark_latest_injection_used(conn, session_id, fact_id)
    conn.commit()
    event_id = int(cursor.lastrowid) if cursor.lastrowid is not None else None
    conn.close()
    return event_id


def usage_snapshot(repo_dir: Path) -> dict[str, sqlite3.Row]:
    ensure_usage_db(repo_dir)
    conn = _connect(usage_path(repo_dir))
    rows = {
        row["fact_id"]: row
        for row in conn.execute("SELECT * FROM usage").fetchall()
    }
    conn.close()
    return rows


def session_fact_rows(repo_dir: Path, session_id: str, limit: int | None = None) -> list[dict[str, Any]]:
    ensure_usage_db(repo_dir)
    conn = _connect(usage_path(repo_dir))
    query = (
        "SELECT * FROM session_fact_state WHERE session_id = ? "
        "ORDER BY COALESCE(last_referenced_turn, 0) DESC, COALESCE(last_referenced_at, '') DESC"
    )
    params: list[Any] = [session_id]
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    rows = [dict(row) for row in conn.execute(query, params).fetchall()]
    conn.close()
    return rows


def latest_referenced_turn(repo_dir: Path, session_id: str) -> int:
    ensure_usage_db(repo_dir)
    conn = _connect(usage_path(repo_dir))
    row = conn.execute(
        """
        SELECT MAX(last_referenced_turn) AS latest_turn
        FROM session_fact_state
        WHERE session_id = ? AND reference_count > 0
        """,
        (session_id,),
    ).fetchone()
    conn.close()
    return int(row["latest_turn"] or 0) if row else 0


def active_working_set(
    repo_dir: Path,
    session_id: str,
    limit: int = 10,
    *,
    exact_turn: int | None = None,
) -> list[dict[str, Any]]:
    ensure_usage_db(repo_dir)
    latest_turn = exact_turn if exact_turn is not None else latest_referenced_turn(repo_dir, session_id)
    if latest_turn <= 0:
        return []
    conn = _connect(usage_path(repo_dir))
    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT *
            FROM session_fact_state
            WHERE session_id = ? AND reference_count > 0 AND last_referenced_turn = ?
            ORDER BY COALESCE(last_referenced_at, '') DESC
            LIMIT ?
            """,
            (session_id, latest_turn, limit),
        ).fetchall()
    ]
    conn.close()
    return rows


def attention_refresh_candidates(
    repo_dir: Path,
    session_id: str,
    *,
    context_window_tokens: int,
    current_session_tokens: int,
    refresh_window_pct: float,
    max_refreshes_per_fact: int,
) -> list[dict[str, Any]]:
    ensure_usage_db(repo_dir)
    threshold_tokens = max(1, int(round(context_window_tokens * refresh_window_pct)))
    conn = _connect(usage_path(repo_dir))
    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT *
            FROM session_fact_state
            WHERE session_id = ?
              AND injection_count > 0
              AND reference_count > 0
              AND refresh_count < ?
            ORDER BY COALESCE(last_referenced_turn, 0) DESC, COALESCE(last_referenced_at, '') DESC
            """,
            (session_id, max_refreshes_per_fact),
        ).fetchall()
    ]
    conn.close()
    return [
        row
        for row in rows
        if current_session_tokens - int(row.get("last_injected_tokens") or 0) >= threshold_tokens
    ]


def session_replay(repo_dir: Path, session_id: str, limit: int | None = None) -> list[dict[str, Any]]:
    ensure_usage_db(repo_dir)
    conn = _connect(usage_path(repo_dir))
    query = (
        "SELECT * FROM usage_events WHERE session_id = ? "
        "ORDER BY event_id ASC"
    )
    params: list[Any] = [session_id]
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    rows = [dict(row) for row in conn.execute(query, params).fetchall()]
    conn.close()
    return rows


def _term_set(text: str) -> set[str]:
    return {
        match.group(0).lower()
        for match in TERM_RE.finditer(text)
        if len(match.group(0)) > 2 and match.group(0).lower() not in REFERENCE_STOPWORDS
    }


def _preview(text: str, limit: int = 160) -> str:
    text = " ".join(text.split())
    return text[:limit]


def detect_referenced_fact_ids(
    repo_dir: Path,
    session_id: str,
    content: str,
    *,
    facts_by_id: dict[str, Any] | None = None,
    limit: int = 20,
) -> list[str]:
    rows = session_fact_rows(repo_dir, session_id)
    if not rows or not content.strip():
        return []
    content_lower = content.lower()
    content_terms = _term_set(content)
    if not content_terms and "01" not in content_lower:
        return []
    if facts_by_id is None:
        facts_by_id = {fact.fact_id: fact for fact in load_all_facts(repo_dir, include_superseded=False)}
    matches: list[tuple[float, str]] = []
    for row in rows:
        fact_id = row["fact_id"]
        fact = facts_by_id.get(fact_id)
        if fact is None:
            continue
        if fact_id.lower() in content_lower:
            matches.append((1.0, fact_id))
            continue
        fact_text = fact.text.strip()
        if len(fact_text) >= 16 and fact_text.lower() in content_lower:
            matches.append((0.95, fact_id))
            continue
        fact_terms = _term_set(fact_text)
        if not fact_terms:
            continue
        shared = content_terms & fact_terms
        overlap_ratio = len(shared) / max(1, len(fact_terms))
        min_terms = min(3, len(fact_terms))
        if len(shared) >= min_terms and overlap_ratio >= 0.6:
            matches.append((overlap_ratio, fact_id))
    matches.sort(key=lambda item: item[0], reverse=True)
    seen: set[str] = set()
    ordered: list[str] = []
    for _, fact_id in matches:
        if fact_id in seen:
            continue
        seen.add(fact_id)
        ordered.append(fact_id)
        if len(ordered) >= limit:
            break
    return ordered


def incremental_rebuild(repo_dir: Path) -> int:
    """Rebuild index for only changed markdown files since last indexed state.

    Uses _meta table to store 'file_hashes' as JSON mapping
    filepath -> content hash (since we may not have git).

    On first call or schema version mismatch: full rebuild.
    Otherwise: only re-index files whose content hash changed.
    Returns count of files re-indexed.
    """
    ensure_index(repo_dir)
    ensure_usage_db(repo_dir)
    conn = _connect(index_path(repo_dir))

    stored = _load_file_hashes(conn)
    if stored is None:
        conn.close()
        rebuild_index(repo_dir)
        return len(list(iter_fact_files(repo_dir)))

    current = _compute_file_hashes(repo_dir)
    changed: list[str] = []
    for rel, digest in current.items():
        if stored.get(rel) != digest:
            changed.append(rel)
    deleted = [rel for rel in stored if rel not in current]

    # Remove entries for deleted files — load facts from old hashes and remove by id
    for rel in deleted:
        path = repo_dir / rel
        # Since the file is deleted, we can't read it. Delete by matching facts
        # whose id was indexed from this file. Use topic as a best-effort match,
        # but also re-scan remaining files afterward to re-add any same-topic facts.
        topic = Path(rel).stem
        conn.execute("DELETE FROM memories WHERE topic = ?", (topic,))

    # Re-index changed/new files
    for rel in changed:
        path = repo_dir / rel
        topic = Path(rel).stem
        conn.execute("DELETE FROM memories WHERE topic = ?", (topic,))
        for fact in read_fact_file(path, repo_dir=repo_dir):
            _insert_fact(conn, fact, repo_dir)

    # If we deleted by topic, re-index any surviving files that share the same stem
    if deleted:
        deleted_stems = {Path(rel).stem for rel in deleted}
        for rel, digest in current.items():
            if Path(rel).stem in deleted_stems and rel not in changed:
                for fact in read_fact_file(repo_dir / rel, repo_dir=repo_dir):
                    _insert_fact(conn, fact, repo_dir)

    _store_file_hashes(conn, current)
    conn.commit()
    conn.close()
    return len(changed) + len(deleted)


def search_sessions(repo_dir: Path, query: str, limit: int = 20) -> list[dict]:
    """Search raw session JSONL files for query terms.

    Returns list of dicts with: session_id, timestamp, role, content_snippet, score.
    Uses simple keyword matching (case-insensitive).
    """
    terms = [t.lower() for t in re.findall(r"[a-zA-Z0-9_]+", query)]
    if not terms:
        return []

    results: list[dict] = []
    for session_id, events in iter_session_payloads(repo_dir, include_archived=True):
        for event in events:
            if "_meta" in event:
                continue
            content = event.get("content", "")
            if not isinstance(content, str) or not content:
                continue
            content_lower = content.lower()
            hits = sum(1 for t in terms if t in content_lower)
            if hits == 0:
                continue
            score = hits / len(terms)
            # Build snippet around first match
            snippet = _build_snippet(content, terms)
            results.append({
                "session_id": session_id,
                "timestamp": event.get("ts", event.get("timestamp", "")),
                "role": event.get("role", ""),
                "content_snippet": snippet,
                "score": score,
            })

    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:limit]


def _build_snippet(content: str, terms: list[str], context_chars: int = 80) -> str:
    content_lower = content.lower()
    best_pos = -1
    for term in terms:
        pos = content_lower.find(term)
        if pos != -1:
            best_pos = pos
            break
    if best_pos == -1:
        return content[:context_chars * 2]
    start = max(0, best_pos - context_chars)
    end = min(len(content), best_pos + context_chars)
    snippet = content[start:end].strip()
    if start > 0:
        snippet = "..." + snippet
    if end < len(content):
        snippet = snippet + "..."
    return snippet


def injected_but_uncited(repo_dir: Path, min_injections: int = 5) -> list[dict]:
    """Find facts that have been injected many times but never cited.
    Returns list of dicts with fact_id, injected_count, cited_count.
    These are candidates for hot-tier demotion."""
    ensure_usage_db(repo_dir)
    conn = _connect(usage_path(repo_dir))
    rows = conn.execute(
        "SELECT fact_id, injected_count, cited_count FROM usage "
        "WHERE injected_count >= ? AND cited_count = 0",
        (min_injections,),
    ).fetchall()
    conn.close()
    return [
        {"fact_id": row["fact_id"], "injected_count": row["injected_count"], "cited_count": row["cited_count"]}
        for row in rows
    ]


def injected_without_reference_sessions(repo_dir: Path, min_sessions: int = 5) -> list[dict]:
    """Find facts injected in many distinct sessions without a matching reference event."""
    ensure_usage_db(repo_dir)
    conn = _connect(usage_path(repo_dir))
    rows = conn.execute(
        """
        SELECT i.fact_id, COUNT(DISTINCT i.session_id) AS silent_sessions
        FROM usage_events i
        WHERE i.event_kind = 'inject'
          AND i.item_kind = 'fact'
          AND i.session_id IS NOT NULL
          AND NOT EXISTS (
            SELECT 1
            FROM usage_events r
            WHERE r.fact_id = i.fact_id
              AND r.session_id = i.session_id
              AND r.event_kind = 'reference'
          )
        GROUP BY i.fact_id
        HAVING COUNT(DISTINCT i.session_id) >= ?
        """,
        (min_sessions,),
    ).fetchall()
    conn.close()
    return [
        {"fact_id": row["fact_id"], "silent_sessions": row["silent_sessions"]}
        for row in rows
    ]
