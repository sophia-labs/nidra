# SPDX-License-Identifier: Apache-2.0
"""The score -> distance mapping of ``GardendVDB`` against the real ``find``.

Runs on a gardend graph that holds the Pagila/Chinook catalog fixture and has
``find`` turned on (``gardend index --graph <id>``): set GARDEND_URL, GARDEND_TOKEN
and GARDEND_GRAPH_ID. Skipped when gardend is unreachable or the graph has no
``find``. No mocks: the scores come from the model's own index.
"""
from __future__ import annotations

import pytest

from auto_ontology.rdf.client import GardendError, client
from auto_ontology.vdb.gardend import distance_from_score

CLARIFY_MAX_DISTANCE = 0.65
DEFAULT_MAX_DISTANCE = 0.75

RELEVANT = [
    "film title", "customer email address", "rental date", "payment amount",
    "replacement cost of a film", "A motion picture available for rental.",
    "A person who rents films from a store.", "Primary key of customer.",
    "store inventory", "film category",
]
NONSENSE = [
    "pension plan contributions", "weather in Lisbon", "quantum chromodynamics lecture",
    "how to bake sourdough bread", "football world cup winner", "medieval castle architecture",
    "stock market volatility index", "volcano eruption in Iceland", "guitar chord progressions",
    "tax return deadline for freelancers",
]


def test_mapping_is_monotone_decreasing_and_never_negative() -> None:
    scores = [i / 100 for i in range(0, 101)]
    distances = [distance_from_score(s) for s in scores]
    assert all(a >= b for a, b in zip(distances, distances[1:]))
    assert min(distances) >= 0.0


@pytest.fixture(scope="module")
def best_distance():
    try:
        c = client()
        c.mcp_call("find", {"graphId": c.graph_id, "query": "film", "k": 1})
    except Exception as exc:  # noqa: BLE001 — unreachable, unauthorised, or find not enabled
        pytest.skip(f"no gardend graph with find: {str(exc)[:120]}")

    def best(query: str) -> float:
        try:
            hits = c.mcp_call("find", {"graphId": c.graph_id, "query": query, "k": 25})["hits"]
        except GardendError as exc:
            pytest.skip(str(exc)[:120])
        return min(distance_from_score(h["score"]) for h in hits)

    return best


def test_relevant_queries_fall_below_the_clarify_threshold(best_distance) -> None:
    worst = max(best_distance(q) for q in RELEVANT)
    assert worst < CLARIFY_MAX_DISTANCE, worst


def test_nonsense_queries_stay_above_the_default_threshold(best_distance) -> None:
    best = min(best_distance(q) for q in NONSENSE)
    assert best > DEFAULT_MAX_DISTANCE, best


def test_the_two_groups_separate(best_distance) -> None:
    assert max(best_distance(q) for q in RELEVANT) < min(best_distance(q) for q in NONSENSE)
