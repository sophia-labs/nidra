# SPDX-License-Identifier: Apache-2.0
"""RDF ports of the ``auto_ontology.dal.sql_attributes`` writes.

Creation of a SqlAttribute is not a DAL function: ``server/sql_attributes/service.py``
``create_sql_attribute`` builds a ``CatalogNode`` and calls
``catalog.store.queries.add_query`` (the catalog-store port), then ``link_to_term``
(ported here) and the embed hook.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from auto_ontology.dal_rdf import _semantic_writes as sw
from auto_ontology.rdf import writes as W
from auto_ontology.rdf.vocab import row_iri_single

PORTS = [
    "detach_existing_sql_edges",
    "link_to_term",
    "update_sql_attribute",
    "set_sql_attribute_description_suggestion",
    "clear_sql_attribute_description_suggestion",
    "clear_sql_attribute_description_suggestions_for_term",
    "delete_sql_attribute_node",
]


def detach_existing_sql_edges(attr_id: str) -> None:
    """Unlink every statement from the attribute, leaving the statements alone."""
    links = sw.link_rows("sql_attribute__sql", ["sql_query_id"], {"attribute_id": attr_id})
    op = sw.delete_link_subjects_op(r["_s"] for r in links)
    if op:
        from auto_ontology.dal_rdf._common import client

        client().update(op)


def link_to_term(attr_id: str, term_id: str) -> None:
    """Point the attribute at exactly one Term, replacing any previous link."""
    if not sw.exists("sql_attribute", attr_id):
        sw.raise_fk("sql_attribute__term", "sql_attribute__term_attribute_id_fkey")
    if not sw.exists("term", term_id):
        sw.raise_fk("sql_attribute__term", "sql_attribute__term_term_id_fkey")
    from auto_ontology.dal_rdf._common import client

    others = [
        r["_s"]
        for r in sw.link_rows("sql_attribute__term", ["term_id"], {"attribute_id": attr_id})
        if r["term_id"] != term_id
    ]
    ops = [sw.delete_link_subjects_op(others)]
    # INSERT DATA of a link row that already exists changes nothing: ON CONFLICT DO NOTHING.
    lines = W.row_triples("sql_attribute__term", {"attribute_id": attr_id, "term_id": term_id})
    ops.append(f"INSERT DATA {{ GRAPH {sw.graph()} {{ {' '.join(lines)} }} }}")
    client().update(" ; ".join(op for op in ops if op))


def update_sql_attribute(
    attr_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    expression: str | None = None,
    source: str | None = None,
    certified: bool | None = None,
    owner_id: str | None = None,
    set_owner: bool = False,
    modified_by: str | None = None,
    touch: bool = False,
) -> None:
    """Set properties on an existing attribute; omitted fields are untouched."""
    changes: dict[str, Any] = {
        k: v
        for k, v in (
            ("name", name),
            ("description", description),
            ("expression", expression),
            ("source", source),
            ("certified", certified),
        )
        if v is not None
    }
    if set_owner:
        changes["owner_id"] = owner_id
    if touch:
        changes["modified"] = datetime.now(timezone.utc)
        changes["modified_by"] = modified_by
    if not changes or not sw.exists("sql_attribute", attr_id):
        return
    if "name" in changes:
        taken = [
            r for r in sw.link_rows("sql_attribute", ["id"], {"name": changes["name"]}) if r["id"] != attr_id
        ]
        if taken:
            sw.raise_unique("sql_attribute", "sql_attribute_name_key")
    W.update_row("sql_attribute", attr_id, changes)


def set_sql_attribute_description_suggestion(attr_id: str, description_suggestion: str) -> None:
    """Cache an LLM description suggestion on the attribute."""
    W.update_row("sql_attribute", attr_id, {"description_suggestion": description_suggestion})


def clear_sql_attribute_description_suggestion(attr_id: str) -> None:
    """Drop the cached suggestion."""
    W.update_row("sql_attribute", attr_id, {"description_suggestion": None})


def clear_sql_attribute_description_suggestions_for_term(term_id: str) -> None:
    """Drop cached suggestions from every SqlAttribute of a Term."""
    links = sw.link_rows("sql_attribute__term", ["attribute_id"], {"term_id": term_id})
    W.update_rows(
        "sql_attribute",
        [row_iri_single("sql_attribute", r["attribute_id"]) for r in links],
        {"description_suggestion": None},
    )


def delete_sql_attribute_node(attr_id: str) -> None:
    """Delete the attribute. Its links go with it, by ``ON DELETE CASCADE``."""
    W.delete_rows("sql_attribute", [attr_id])
