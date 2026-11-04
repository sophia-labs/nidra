# SPDX-License-Identifier: Apache-2.0
"""RDF ports of the remaining ``auto_ontology.dal.custom_analyses`` reads."""
from __future__ import annotations

from typing import Any

from auto_ontology.dal_rdf._common import P, class_iri, client, iri, lit, row_id, row_iri_single, wrap
from auto_ontology.dal_rdf.sql_attributes import owned_table_ids, read_rows, statement_texts

PORTS = [
    "find_analysis_by_name",
    "get_custom_analysis_by_id",
    "list_custom_analyses",
    "count_custom_analyses",
    "find_analysis_by_sql",
    "fetch_database_name_for_analysis",
]


def _matching(zone_ids: list[str] | None, search: str | None) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Analyses with a statement, inside the zones, whose name contains *search*."""
    rows = read_rows("custom_analysis")
    texts = statement_texts("custom_analysis__sql", "analysis_id")
    out = [r for r in rows.values() if r["id"] in texts]
    if zone_ids is not None and out:
        from auto_ontology.dal import users

        allowed = set(users.resolve_accessible_catalog_ids(zone_ids)["table_ids"])
        owned = owned_table_ids("custom_analysis__sql", "analysis_id", [r["id"] for r in out])
        out = [r for r in out if all(t in allowed for t in owned.get(r["id"], []))]
    if search and search.strip():
        needle = search.strip().lower()
        out = [r for r in out if r["name"] is not None and needle in r["name"].lower()]
    return out, texts


def list_custom_analyses(
    zone_ids: list[str] | None = None,
    *,
    search: str | None = None,
    skip: int = 0,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    rows, texts = _matching(zone_ids, search)
    rows.sort(key=lambda r: ((r["name"] or "").lower(), r["id"]))
    if skip:
        rows = rows[skip:]
    if limit is not None:
        rows = rows[:limit]
    return [
        {"id": r["id"], "name": r["name"], "description": r["description"], "sql": texts.get(r["id"])}
        for r in rows
    ]


def count_custom_analyses(zone_ids: list[str] | None = None, *, search: str | None = None) -> int:
    return len(_matching(zone_ids, search)[0])


def find_analysis_by_name(name: str, exclude_id: str | None) -> dict[str, str] | None:
    body = f"?a a {iri(class_iri('custom_analysis'))} ; {P.custom_analysis.name} ?n . FILTER(?n = {lit(name)})"
    rows = client().select(f"SELECT ?a ?n WHERE {{ {wrap(body)} }}")
    found = sorted((row_id(r["a"]), r["n"]) for r in rows if exclude_id is None or row_id(r["a"]) != exclude_id)
    return {"id": found[0][0], "name": found[0][1]} if found else None


def find_analysis_by_sql(sql: str, exclude_id: str | None) -> dict[str, str] | None:
    body = (
        f"?l a {iri(class_iri('custom_analysis__sql'))} ; {P.custom_analysis__sql.ref_analysis_id} ?a ; "
        f"{P.custom_analysis__sql.ref_sql_query_id} ?q . ?q {P.sql_query.sql_full_query} ?text . "
        f"FILTER(?text = {lit(sql)}) ?a {P.custom_analysis.name} ?n"
    )
    rows = client().select(f"SELECT DISTINCT ?a ?n WHERE {{ {wrap(body)} }}")
    found = sorted((row_id(r["a"]), r["n"]) for r in rows if exclude_id is None or row_id(r["a"]) != exclude_id)
    return {"id": found[0][0], "name": found[0][1]} if found else None


def get_custom_analysis_by_id(analysis_id: str) -> str | None:
    s = iri(row_iri_single("custom_analysis", analysis_id))
    body = f"{s} a {iri(class_iri('custom_analysis'))}"
    return analysis_id if client().select(f"SELECT ?x WHERE {{ {wrap(body + ' . BIND(1 AS ?x)')} }} LIMIT 1") else None


def fetch_database_name_for_analysis(analysis_id: str) -> str | None:
    body = (
        f"?l a {iri(class_iri('custom_analysis__sql'))} ; "
        f"{P.custom_analysis__sql.ref_analysis_id} {iri(row_iri_single('custom_analysis', analysis_id))} ; "
        f"{P.custom_analysis__sql.ref_sql_query_id} ?q . "
        f"?qt a {iri(class_iri('sql_query__table'))} ; {P.sql_query__table.ref_sql_query_id} ?q ; "
        f"{P.sql_query__table.ref_table_id} ?t . "
        f"?t {P.catalog_table.ref_schema_id} ?sch . ?sch {P.catalog_schema.ref_database_id} ?db . "
        f"?db {P.catalog_database.name} ?name"
    )
    rows = client().select(f"SELECT DISTINCT ?name WHERE {{ {wrap(body)} }}")
    return str(min(r["name"] for r in rows)) if rows else None
