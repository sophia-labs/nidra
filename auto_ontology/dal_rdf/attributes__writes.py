# SPDX-License-Identifier: Apache-2.0
"""RDF ports of the ``auto_ontology.dal.attributes`` writes: ColumnAttribute
upsert and update, and the SEMANTIC_FK edge."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.exc import IntegrityError

from auto_ontology.dal_rdf._common import P, class_iri, client, iri, lit, row_id, row_iri_single, values, wrap
from auto_ontology.dal_rdf._wdata import apply_cells, existing, link_iri, no_transaction
from auto_ontology.dal_rdf.attributes import fetch_attr_column_contexts as _fetch_attr_column_contexts
from auto_ontology.rdf import writes as W
from auto_ontology.rdf.client import RDF_JSON
from auto_ontology.semantic.constants import SEMANTIC_SOURCE

logger = logging.getLogger(__name__)

PORTS = [
    "merge_column_attribute",
    "update_column_attribute",
    "merge_semantic_fk",
    "delete_semantic_fk",
    "set_semantic_fk_join_default",
]


def _sel(sparql: str) -> list[dict[str, Any]]:
    return client().select(sparql)


def merge_column_attribute(
    *,
    term_name: str,
    table_id: str,
    source_column: str,
    attr_name: str,
    datatype: str,
    description: str | None,
) -> str | None:
    """Upsert a ColumnAttribute and link it to its column and Term (``None`` if either is missing)."""
    column = _sel(
        "SELECT ?c WHERE { "
        + wrap(
            f"?c a {iri(class_iri('catalog_column'))} ; {P.catalog_column.ref_table_id} "
            f"{iri(row_iri_single('catalog_table', table_id))} ; {P.catalog_column.name} {lit(source_column)} ."
        )
        + " } ORDER BY ?c LIMIT 1"
    )
    term = _sel(
        "SELECT ?t WHERE { "
        + wrap(
            f"?t a {iri(class_iri('term'))} ; {P.term.name} {lit(term_name)} ; {P.term.source} {lit(SEMANTIC_SOURCE)} ."
        )
        + " } LIMIT 1"
    )
    if not column or not term:
        return None
    column_id, term_id = row_id(column[0]["c"]), row_id(term[0]["t"])

    # The 5-part merge key (uq_column_attribute_merge_key).
    found = _sel(
        "SELECT ?a WHERE { "
        + wrap(
            f"?a a {iri(class_iri('column_attribute'))} ; {P.column_attribute.name} {lit(attr_name)} ; "
            f"{P.column_attribute.source_column} {lit(source_column)} ; {P.column_attribute.term_name} {lit(term_name)} ; "
            f"{P.column_attribute.table_id} {lit(table_id)} ; {P.column_attribute.source} {lit(SEMANTIC_SOURCE)} ."
        )
        + " } LIMIT 1"
    )
    inserts: list[str] = []
    cells = {}
    if found:
        attribute_id = row_id(found[0]["a"])
        changes: dict[str, Any] = {"datatype": datatype}  # assigned, as the SQL does (None clears)
        if description is not None:
            changes["description"] = description  # coalesce(new, existing)
        cells = {"column_attribute": {found[0]["a"]: changes}}
    else:
        stored = W.apply_defaults(
            "column_attribute",
            {
                "name": attr_name,
                "source_column": source_column,
                "term_name": term_name,
                "table_id": table_id,
                "source": SEMANTIC_SOURCE,
                "datatype": datatype,
                "description": description,
            },
        )
        attribute_id = stored["id"]
        inserts.extend(W.row_triples("column_attribute", stored))

    # ON CONFLICT DO NOTHING (no target): any unique violation skips the insert,
    # which for the Term link includes UNIQUE(attribute_id).
    has_iri = link_iri("column__has_attribute", column_id=column_id, attribute_id=attribute_id)
    if has_iri not in existing("column__has_attribute", [has_iri]):
        inserts.extend(W.row_triples("column__has_attribute", {"column_id": column_id, "attribute_id": attribute_id}))
    term_links = _sel(
        "SELECT ?l WHERE { "
        + wrap(f"?l {P.column_attribute__term.ref_attribute_id} {iri(row_iri_single('column_attribute', attribute_id))} .")
        + " } LIMIT 1"
    )
    if not term_links:
        inserts.extend(W.row_triples("column_attribute__term", {"attribute_id": attribute_id, "term_id": term_id}))
    with no_transaction():
        apply_cells(cells, inserts)
    return attribute_id


def update_column_attribute(
    attr_id: str,
    term_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    certified: bool | None = None,
    modified_by: str | None = None,
    touch: bool = False,
) -> dict[str, Any] | None:
    """Update an attribute's metadata (every field coalesced) and return the embed context."""
    attr = row_iri_single("column_attribute", attr_id)
    owned = link_iri("column_attribute__term", attribute_id=attr_id, term_id=term_id)
    if owned not in existing("column_attribute__term", [owned]):
        return None
    if name is not None:
        # The rename lands on the merge key: another attribute holding it is the
        # unique violation Postgres raises (and upstream deliberately lets surface).
        key = _sel(
            "SELECT ?sc ?tn ?ti ?src WHERE { "
            + wrap(
                f"{iri(attr)} {P.column_attribute.source_column} ?sc ; {P.column_attribute.term_name} ?tn ; "
                f"{P.column_attribute.table_id} ?ti ; {P.column_attribute.source} ?src ."
            )
            + " }"
        )
        if key:
            k = key[0]
            clash = _sel(
                "SELECT ?o WHERE { "
                + wrap(
                    f"?o a {iri(class_iri('column_attribute'))} ; {P.column_attribute.name} {lit(name)} ; "
                    f"{P.column_attribute.source_column} {lit(k['sc'])} ; {P.column_attribute.term_name} {lit(k['tn'])} ; "
                    f"{P.column_attribute.table_id} {lit(k['ti'])} ; {P.column_attribute.source} {lit(k['src'])} . "
                    f"FILTER(?o != {iri(attr)})"
                )
                + " } LIMIT 1"
            )
            if clash:
                raise IntegrityError(
                    "UPDATE column_attribute SET name=...",
                    {},
                    Exception('duplicate key value violates unique constraint "uq_column_attribute_merge_key"'),
                )
    changes: dict[str, Any] = {}
    if name is not None:
        changes["name"] = name
    if description is not None:
        changes["description"] = description
    if certified is not None:
        changes["certified"] = certified
    if touch:
        changes["modified"] = datetime.now(timezone.utc)
        changes["modified_by"] = modified_by
    apply_cells({"column_attribute": {attr: changes}})

    rows = _sel(
        "SELECT ?name ?description ?term_name ?source_column ?certified ?col ?sv ?dbn ?syn WHERE { "
        + wrap(
            f"{iri(attr)} {P.column_attribute.name} ?name ; {P.column_attribute.term_name} ?term_name ; "
            f"{P.column_attribute.source_column} ?source_column . "
            f"OPTIONAL {{ {iri(attr)} {P.column_attribute.description} ?description }} "
            f"OPTIONAL {{ {iri(attr)} {P.column_attribute.certified} ?certified }} "
            f"OPTIONAL {{ {iri(row_iri_single('term', term_id))} {P.term.synonyms} ?syn "
            f"FILTER(datatype(?syn) = <{RDF_JSON}>) }} "
            f"OPTIONAL {{ ?h {P.column__has_attribute.ref_attribute_id} {iri(attr)} ; "
            f"{P.column__has_attribute.ref_column_id} ?col . "
            f"OPTIONAL {{ ?col {P.catalog_column.sample_values} ?sv }} "
            f"OPTIONAL {{ ?col {P.catalog_column.ref_table_id} ?t . ?t {P.catalog_table.ref_schema_id} ?sch . "
            f"?sch {P.catalog_schema.ref_database_id} ?db . ?db {P.catalog_database.name} ?dbn }} }}"
        )
        + " }"
    )
    if not rows:
        return None
    rows.sort(key=lambda r: (r.get("col") is None, r.get("col") or ""))  # ORDER BY column id, LIMIT 1
    r = rows[0]
    return {
        "id": attr_id,
        "name": r["name"],
        "description": r.get("description"),
        "term_name": r["term_name"],
        "source_column": r["source_column"],
        "certified": r.get("certified"),
        "column_id": row_id(r["col"]) if r.get("col") else None,
        "sample_values": r.get("sv"),
        "term_id": term_id,
        "term_synonyms": r.get("syn"),
        "database_name": r.get("dbn"),
    }


def _database_name_of_column(column_id: str) -> str:
    rows = _sel(
        "SELECT ?dbn WHERE { "
        + wrap(
            f"{iri(row_iri_single('catalog_column', column_id))} {P.catalog_column.ref_table_id} ?t . "
            f"?t {P.catalog_table.ref_schema_id} ?sch . ?sch {P.catalog_schema.ref_database_id} ?db . "
            f"?db {P.catalog_database.name} ?dbn ."
        )
        + " }"
    )
    return (rows[0]["dbn"] if rows else "") or ""


def _same_database(src_column_id: str, tgt_attr_id: str) -> bool:
    col_db = _database_name_of_column(src_column_id)
    attr_db = _fetch_attr_column_contexts([tgt_attr_id], database_name=None).get(tgt_attr_id, {}).get("database_name")
    return bool(col_db) and col_db == attr_db


def merge_semantic_fk(src_column_id: str, tgt_attr_id: str) -> bool:
    """Point a column at the attribute it references. Idempotent; refuses across databases."""
    if not _same_database(src_column_id, tgt_attr_id):
        logger.warning("merge_semantic_fk: refusing cross-database edge %s -> %s", src_column_id, tgt_attr_id)
        return False
    edge = link_iri("column__semantic_fk", column_id=src_column_id, attribute_id=tgt_attr_id)
    if edge not in existing("column__semantic_fk", [edge]):
        W.insert_row("column__semantic_fk", {"column_id": src_column_id, "attribute_id": tgt_attr_id})
    return True


def delete_semantic_fk(src_column_id: str, tgt_attr_id: str) -> None:
    """Remove one ``SEMANTIC_FK`` edge; a no-op when it is not there."""
    W.delete_subjects(
        "column__semantic_fk",
        [link_iri("column__semantic_fk", column_id=src_column_id, attribute_id=tgt_attr_id)],
    )


def set_semantic_fk_join_default(source_table_id: str, target_attribute_id: str, selected_column_id: str) -> None:
    """Enable one semantic FK in a source-table/target group, disable the others."""
    group = _sel(
        "SELECT ?r ?col WHERE { "
        + wrap(
            f"?r {P.column__semantic_fk.ref_attribute_id} {iri(row_iri_single('column_attribute', target_attribute_id))} ; "
            f"{P.column__semantic_fk.ref_column_id} ?col . "
            f"?col {P.catalog_column.ref_table_id} {iri(row_iri_single('catalog_table', source_table_id))} ."
        )
        + " }"
    )
    if selected_column_id not in {row_id(r["col"]) for r in group}:
        # Upstream raises inside its transaction, which rolls the update back: nothing is written.
        raise ValueError("Selected semantic FK column does not belong to the requested group")
    apply_cells(
        {
            "column__semantic_fk": {
                r["r"]: {"use_in_join_paths": row_id(r["col"]) == selected_column_id} for r in group
            }
        }
    )
