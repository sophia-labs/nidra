# SPDX-License-Identifier: Apache-2.0
"""RDF ports of ``auto_ontology.dal.attributes`` reads (scope lane).

Named ``attributes__scope`` so it still targets ``auto_ontology.dal.attributes``
without touching the sibling lane's ``attributes.py``.
"""
from __future__ import annotations

from typing import Any

from auto_ontology.dal_rdf._common import P, client, values, wrap
from auto_ontology.rdf.vocab import row_id, row_iri_single
from auto_ontology.semantic.constants import SEMANTIC_SOURCE

PORTS = [
    "fetch_column_attribute_columns_map",
    "find_column_attribute_by_column_id",
    "find_unlinked_fk_columns",
]


def _column_paths(link_table: str, attr_ids: list[str]) -> list[dict[str, Any]]:
    attrs = [row_iri_single("column_attribute", a) for a in dict.fromkeys(attr_ids)]
    link = getattr(P, link_table)
    rows = client().select(
        "SELECT ?attr ?col ?column_name ?tb ?table_name ?sc ?db WHERE { "
        + wrap(
            f"{values('attr', attrs)} "
            f"?link {link.ref_attribute_id} ?attr ; {link.ref_column_id} ?col . "
            f"?col {P.catalog_column.name} ?column_name ; {P.catalog_column.ref_table_id} ?tb . "
            f"?tb {P.catalog_table.name} ?table_name ; {P.catalog_table.ref_schema_id} ?sc . "
            f"?sc {P.catalog_schema.ref_database_id} ?db"
        )
        + " }"
    )
    return [
        {
            "attr_id": row_id(r["attr"]),
            "id": row_id(r["col"]),
            "column_name": r["column_name"],
            "table_id": row_id(r["tb"]),
            "table_name": r["table_name"],
            "schema_id": row_id(r["sc"]),
            "db_id": row_id(r["db"]),
        }
        for r in rows
    ]


def _column_path_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {k: row[k] for k in ("id", "column_name", "table_id", "table_name", "schema_id", "db_id")}


def fetch_column_attribute_columns_map(attr_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not attr_ids:
        return {}
    ids = list(attr_ids)
    primary = sorted(_column_paths("column__has_attribute", ids), key=lambda r: r["id"])
    referenced = sorted(
        _column_paths("column__semantic_fk", ids), key=lambda r: (r["table_name"], r["column_name"])
    )
    result: dict[str, dict[str, Any]] = {
        attr_id: {"primary_column": None, "referenced_columns": []} for attr_id in ids
    }
    for row in primary:
        entry = result.get(row["attr_id"])
        if entry is not None and entry["primary_column"] is None:
            entry["primary_column"] = _column_path_dict(row)
    for row in referenced:
        entry = result.get(row["attr_id"])
        if entry is not None:
            entry["referenced_columns"].append(_column_path_dict(row))
    return result


def find_column_attribute_by_column_id(column_id: str) -> str | None:
    from auto_ontology.dal_rdf._common import lit

    rows = client().select(
        "SELECT ?attr WHERE { "
        + wrap(
            f"?link {P.column__has_attribute.ref_column_id} {'<' + row_iri_single('catalog_column', column_id) + '>'} ; "
            f"      {P.column__has_attribute.ref_attribute_id} ?attr . "
            f"?attr {P.column_attribute.source} {lit(SEMANTIC_SOURCE)}"
        )
        + " }"
    )
    ids = sorted(row_id(r["attr"]) for r in rows)
    return ids[0] if ids else None


def find_unlinked_fk_columns(database_name: str | None = None) -> list[dict[str, Any]]:
    from auto_ontology.dal_rdf._common import lit

    scope = ""
    if database_name is not None:
        scope = (
            f"?tb {P.catalog_table.ref_schema_id} ?sc . ?sc {P.catalog_schema.ref_database_id} ?db . "
            f"?db {P.catalog_database.name} {lit(database_name)} . "
        )
    opts = " ".join(
        f"OPTIONAL {{ ?c {getattr(P.catalog_column, col)} ?{col} }}"
        for col in ("description", "sample_values", "is_unique")
    )
    rows = client().select(
        "SELECT ?c ?name ?description ?sample_values ?is_unique ?tb ?table_name ?fk_col ?fk_tb WHERE { "
        + wrap(
            f"?c a <urn:ao:catalog_column> ; {P.catalog_column.name} ?name ; "
            f"   {P.catalog_column.ref_table_id} ?tb . "
            f"?tb {P.catalog_table.name} ?table_name . {scope} {opts} "
            f"OPTIONAL {{ ?fk {P.column__foreign_key.ref_source_column_id} ?c ; "
            f"               {P.column__foreign_key.ref_target_column_id} ?fk_col . "
            f"            OPTIONAL {{ ?fk_col {P.catalog_column.ref_table_id} ?fk_tb }} }} "
            f"FILTER NOT EXISTS {{ ?l1 {P.column__semantic_fk.ref_column_id} ?c }} "
            f"FILTER NOT EXISTS {{ ?l2 {P.column__has_attribute.ref_column_id} ?c }}"
        )
        + " }"
    )
    out = [
        {
            "id": row_id(r["c"]),
            "name": r["name"],
            "description": r.get("description"),
            "sample_values": r.get("sample_values"),
            "is_unique": r.get("is_unique"),
            "table_id": row_id(r["tb"]),
            "table_name": r["table_name"],
            "fk_target_col_id": row_id(r["fk_col"]) if r.get("fk_col") else None,
            "fk_target_table_id": row_id(r["fk_tb"]) if r.get("fk_tb") else None,
        }
        for r in rows
    ]
    out.sort(key=lambda d: (d["id"], d["fk_target_col_id"] or ""))
    return out
