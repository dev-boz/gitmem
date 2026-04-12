from __future__ import annotations

import re

from umx.models import Fact
from umx.strength import conflict_winner


STOPWORDS = {"the", "a", "an", "in", "on", "at", "for", "of", "to", "and"}


def _terms(text: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r"[a-zA-Z0-9_]+", text)
        if token.lower() not in STOPWORDS
    }


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+", text))


def facts_conflict(left: Fact, right: Fact) -> bool:
    if left.fact_id == right.fact_id:
        return False
    if left.topic != right.topic:
        return False
    if left.applies_to and right.applies_to and not left.applies_to.overlaps(right.applies_to):
        return False
    shared = len(_terms(left.text) & _terms(right.text))
    numbers_left = _numbers(left.text)
    numbers_right = _numbers(right.text)
    negation = (" not " in f" {left.text.lower()} ") ^ (" not " in f" {right.text.lower()} ")
    return shared >= 2 and (negation or (numbers_left and numbers_right and numbers_left != numbers_right))


def resolve_conflict(left: Fact, right: Fact, config=None) -> tuple[Fact, Fact]:
    winner = conflict_winner(left, right, config=config)
    loser = right if winner.fact_id == left.fact_id else left
    if loser.fact_id not in winner.conflicts_with:
        winner.conflicts_with.append(loser.fact_id)
    if winner.fact_id not in loser.conflicts_with:
        loser.conflicts_with.append(winner.fact_id)
    if not winner.supersedes:
        winner.supersedes = loser.fact_id
    loser.superseded_by = winner.fact_id
    return winner, loser
