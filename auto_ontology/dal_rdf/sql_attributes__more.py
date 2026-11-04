# SPDX-License-Identifier: Apache-2.0
"""RDF ports of the remaining ``auto_ontology.dal.sql_attributes`` reads.

Reuses the private helpers of ``sql_attributes.py`` (``_attribute_rows``,
``_zone_filtered``); ``fetch_sql_attribute_counts`` has its own zone filter
because it also takes ``data_ids_by_zone``.
"""
from __future__ import annotations

from typing import Any

from auto_ontology.dal_rdf._common import P, class_iri, client, iri, row_id, row_iri_single, wrap
from auto_ontology.dal_rdf.sql_attributes import _attribute_rows, _zone_filtered, owned_table_ids

PORTS = [
    "count_sql_attributes_by_term_id",
    "fetch_sql_attribute_counts",
    "fetch_sql_attribute_docs",
    "get_sql_attribute_by_id",
    "list_sql_attributes",
    "find_attr_by_expression",
]


def list_sql_attributes() -> list[dict[str, Any]]:
    rows = _attribute_rows()
    rows.sort(key=lambda r: (r["name"] or "", r["id"], r["term_id"]))
    return rows


def fetch_sql_attribute_counts(
    zone_ids: list[str] | None = None,
    data_ids_by_zone: dict[str, set[str]] | None = None,
    term_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    if term_ids is not None and not term_ids:  # `IN ()` matches nothing
        return []
    rows = _attribute_rows()
    if zone_ids is not None and rows:
        from auto_ontology.dal import users

        allowed = set(users.resolve_accessible_catalog_ids(zone_ids, data_ids_by_zone)["table_ids"])
        owned = owned_table_ids("sql_attribute__sql", "attribute_id", sorted({r["id"] for r in rows}))
        rows = [r for r in rows if all(t in allowed for t in owned.get(r["id"], []))]
    if term_ids is not None:
        wanted = set(term_ids)
        rows = [r for r in rows if r["term_id"] in wanted]
    per_term: dict[str, set[str]] = {}
    for r in rows:
        per_term.setdefault(r["term_id"], set()).add(r["id"])
    return [{"term_id": t, "count": len(a)} for t, a in sorted(per_term.items())]


def count_sql_attributes_by_term_id(term_id: str, zone_ids: list[str] | None = None) -> int:
    rows = _zone_filtered(_attribute_rows(term_id=term_id), zone_ids)
    return len({r["id"] for r in rows})


def find_attr_by_expression(*, term_id: str, expression: str, exclude_id: str | None) -> dict[str, str] | None:
    normalized = " ".join(expression.split()).lower()
    body = (
        f"?l a {iri(class_iri('sql_attribute__term'))} ; {P.sql_attribute__term.ref_term_id} "
        f"{iri(row_iri_single('term', term_id))} ; {P.sql_attribute__term.ref_attribute_id} ?a . "
        f"?a {P.sql_attribute.name} ?n . OPTIONAL {{ ?a {P.sql_attribute.expression} ?e }}"
    )
    rows = client().select(f"SELECT DISTINCT ?a ?n ?e WHERE {{ {wrap(body)} }}")
    for r in sorted(rows, key=lambda r: r["a"]):
        aid = row_id(r["a"])
        if exclude_id is not None and aid == exclude_id:
            continue
        candidate = r.get("e")
        if isinstance(candidate, str) and " ".join(candidate.split()).lower() == normalized:
            return {"id": aid, "name": r["n"]}
    return None


def get_sql_attribute_by_id(attr_id: str) -> str | None:
    body = f"{iri(row_iri_single('sql_attribute', attr_id))} a {iri(class_iri('sql_attribute'))} . BIND(1 AS ?x)"
    return attr_id if client().select(f"SELECT ?x WHERE {{ {wrap(body)} }} LIMIT 1") else None


def fetch_sql_attribute_docs(attr_id: str) -> list[dict[str, Any]]:
    rows = sorted(_attribute_rows(attr_ids=[attr_id]), key=lambda r: r["term_id"])
    docs: list[dict[str, Any]] = []
    for row in rows:
        description = row["description"]
        text = f"sql_attribute: {row['name']}"
        if description is not None and str(description).strip():
            text += f", description: {description}"
        if row["term_name"] is not None:
            text += f", term: {row['term_name']}"
        text += f", sql: {row['sql']}"
        docs.append(
            {
                "text": text,
                "name": row["name"],
                "label": "SqlAttribute",
                "id": row["id"],
                "source": row["source"] or "",
            }
        )
    return docs
