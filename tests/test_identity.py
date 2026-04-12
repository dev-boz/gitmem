from __future__ import annotations

from umx.identity import generate_fact_id, semantic_dedup_key


def test_generate_fact_id_shape() -> None:
    fact_id = generate_fact_id()
    assert len(fact_id) == 26
    assert fact_id == fact_id.upper()


def test_semantic_dedup_key_stable() -> None:
    left = semantic_dedup_key("Postgres runs on 5433", "project", "devenv")
    right = semantic_dedup_key("postgres runs on 5433", "project", "devenv")
    other = semantic_dedup_key("postgres runs on 5432", "project", "devenv")
    assert left == right
    assert left != other
