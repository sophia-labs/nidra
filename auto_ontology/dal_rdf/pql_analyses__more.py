# SPDX-License-Identifier: Apache-2.0
"""RDF ports of the remaining ``auto_ontology.dal.pql_analyses`` reads."""
from __future__ import annotations

from typing import Any

from auto_ontology.dal_rdf._common import P, class_iri, client, iri, lit, row_id, row_iri_single, wrap
from auto_ontology.dal_rdf.sql_attributes import read_rows

PORTS = [
    "find_pql_analysis_by_name",
    "get_pql_analysis_by_id",
    "list_pql_analyses",
    "count_pql_analyses",
    "find_pql_analysis_by_pql",
]


def _matching(search: str | None) -> list[dict[str, Any]]:
    rows = list(read_rows("pql_analysis").values())
    if search and search.strip():
        needle = search.strip().lower()
        rows = [r for r in rows if r["name"] is not None and needle in r["name"].lower()]
    return rows


def list_pql_analyses(*, search: str | None = None, skip: int = 0, limit: int | None = None) -> list[dict[str, Any]]:
    rows = _matching(search)
    rows.sort(key=lambda r: ((r["name"] or "").lower(), r["id"]))
    if skip:
        rows = rows[skip:]
    if limit is not None:
        rows = rows[:limit]
    return [{"id": r["id"], "name": r["name"], "description": r["description"], "pql": r["pql"]} for r in rows]


def count_pql_analyses(*, search: str | None = None) -> int:
    return len(_matching(search))


def _find_conflict(column: str, value: str, exclude_id: str | None) -> dict[str, str] | None:
    body = (
        f"?a a {iri(class_iri('pql_analysis'))} ; {getattr(P.pql_analysis, column)} ?v ; "
        f"{P.pql_analysis.name} ?n . FILTER(?v = {lit(value)})"
    )
    rows = client().select(f"SELECT ?a ?n WHERE {{ {wrap(body)} }}")
    found = sorted((row_id(r["a"]), r["n"]) for r in rows if exclude_id is None or row_id(r["a"]) != exclude_id)
    return {"id": found[0][0], "name": found[0][1]} if found else None


def find_pql_analysis_by_name(name: str, exclude_id: str | None) -> dict[str, str] | None:
    return _find_conflict("name", name, exclude_id)


def find_pql_analysis_by_pql(pql: str, exclude_id: str | None) -> dict[str, str] | None:
    return _find_conflict("pql", pql, exclude_id)


def get_pql_analysis_by_id(analysis_id: str) -> str | None:
    body = f"{iri(row_iri_single('pql_analysis', analysis_id))} a {iri(class_iri('pql_analysis'))} . BIND(1 AS ?x)"
    return analysis_id if client().select(f"SELECT ?x WHERE {{ {wrap(body)} }} LIMIT 1") else None
