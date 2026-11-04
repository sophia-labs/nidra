# SPDX-License-Identifier: Apache-2.0
"""RDF ports of the ``auto_ontology.dal.terms`` writes."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from auto_ontology.dal_rdf import _semantic_writes as sw
from auto_ontology.dal_rdf._common import P, client, iri, lit, row_id, row_iri_single, class_iri, wrap
from auto_ontology.rdf import writes as W
from auto_ontology.semantic.constants import SEMANTIC_SOURCE

PORTS = ["update_term", "upsert_table_term"]


def _term_by_name_source(name: str, source: str) -> list[dict[str, Any]]:
    """``[{id, table_id | None}]``: the term(s) with this name/source, outer-joined to table__term."""
    body = (
        f"?t a {iri(class_iri('term'))} ; {P.term.name} {lit(name)} ; {P.term.source} {lit(source)} . "
        f"OPTIONAL {{ ?l a {iri(class_iri('table__term'))} ; {P.table__term.ref_term_id} ?t ; {P.table__term.table_id} ?tb }}"
    )
    return [
        {"id": row_id(r["t"]), "table_id": r.get("tb")}
        for r in client().select(f"SELECT ?t ?tb WHERE {{ {wrap(body)} }}")
    ]


def update_term(
    term_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    name_certified: bool | None = None,
    description_certified: bool | None = None,
    owner_id: str | None = None,
    set_owner: bool = False,
    modified_by: str | None = None,
    touch: bool = False,
) -> dict[str, Any] | None:
    """Update a Term, returning old and new values, or ``None`` when missing."""
    previous = sw.read_rows("term", [term_id]).get(term_id)
    if previous is None:
        return None
    old_name = previous["name"]

    changes: dict[str, Any] = {}
    for column, value in (
        ("name", name),
        ("description", description),
        ("name_certified", name_certified),
        ("description_certified", description_certified),
    ):
        if value is not None:
            changes[column] = value
    if set_owner:
        changes["owner_id"] = owner_id
    if touch:
        changes["modified"] = datetime.now(timezone.utc)
        changes["modified_by"] = modified_by

    new_name = changes.get("name", previous["name"])
    if new_name != old_name and any(
        r["id"] != term_id for r in _term_by_name_source(new_name, previous["source"])
    ):
        sw.raise_unique("term", "uq_term_name_source")

    if changes:
        W.update_row("term", term_id, changes)
    result = {
        "id": term_id,
        "name": new_name,
        "description": changes.get("description", previous["description"]),
        "name_certified": changes.get("name_certified", previous["name_certified"]),
        "description_certified": changes.get("description_certified", previous["description_certified"]),
        "owner_id": changes["owner_id"] if set_owner else previous["owner_id"],
        "old_name": old_name,
    }

    # The denormalised term_name copy on every ColumnAttribute of the Term.
    attrs = sw.link_rows("column_attribute__term", ["attribute_id"], {"term_id": term_id})
    W.update_rows(
        "column_attribute",
        [row_iri_single("column_attribute", a["attribute_id"]) for a in attrs],
        {"term_name": result["name"]},
    )
    return result


def upsert_table_term(
    name: str,
    description: str,
    table_id: str,
    synonyms: list[str] | None = None,
) -> tuple[str, str] | None:
    """Upsert a compilation Term owned by one table (see the upstream docstring)."""
    table = sw.read_rows("catalog_table", [table_id]).get(table_id)
    if table is None:
        return None
    schema = sw.read_rows("catalog_schema", [table["schema_id"]]).get(table["schema_id"]) if table["schema_id"] else None
    database = (
        sw.read_rows("catalog_database", [schema["database_id"]]).get(schema["database_id"])
        if schema and schema["database_id"]
        else None
    )
    qualified_table = ".".join(
        part
        for part in (database["name"] if database else None, schema["name"] if schema else None, table["name"])
        if part
    )
    candidate_names = (name, f"{name} ({qualified_table})", f"{name} ({table_id})")

    for candidate_name in candidate_names:
        existing = _term_by_name_source(candidate_name, SEMANTIC_SOURCE)
        represented_tables = {row["table_id"] for row in existing if row.get("table_id")}
        if existing and represented_tables == {table_id}:
            term_id = existing[0]["id"]
            W.update_row("term", term_id, {"description": description, "synonyms": synonyms or []})
            return term_id, candidate_name
        if existing:
            continue
        term_id = str(uuid.uuid4())
        sw.insert_batch(
            {
                "term": [
                    {
                        "id": term_id,
                        "name": candidate_name,
                        "source": SEMANTIC_SOURCE,
                        "description": description,
                        "synonyms": synonyms or [],
                    }
                ],
                "table__term": [{"table_id": table_id, "term_id": term_id}],
            }
        )
        return term_id, candidate_name

    raise RuntimeError(f"Could not allocate a private Term name for table {table_id}")
