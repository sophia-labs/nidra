# SPDX-License-Identifier: Apache-2.0
"""RDF port of ``auto_ontology.dal.candidates.expand_info``.

The dispatch and the per-label shapes are upstream's; only the reads differ.
Whole-row reads (``select(table)``) come from ``read_rows``, which returns every
column of the table as the Postgres row would.
"""
from __future__ import annotations

import logging
from typing import Any

from auto_ontology.catalog.constants import Labels
from auto_ontology.dal_rdf._common import P, client, iri, wrap
from auto_ontology.dal_rdf.sql_attributes import (
    column_descriptions,
    ids_values,
    owned_table_ids,
    read_rows,
    statement_texts,
)
from auto_ontology.rdf.vocab import class_iri, row_id
from auto_ontology.utils.sample_values import stringify_sample_values

logger = logging.getLogger(__name__)

PORTS = ["expand_info"]

_NODE_TABLES = {
    Labels.DB: "catalog_database",
    Labels.SCHEMA: "catalog_schema",
    Labels.TABLE: "catalog_table",
    Labels.COLUMN: "catalog_column",
    Labels.SQL: "sql_query",
    Labels.CUSTOM_ANALYSIS: "custom_analysis",
}


def _table_payloads(table_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not table_ids:
        return {}
    rows = read_rows("catalog_table", table_ids)
    # Names of the schema and database, through the inner joins.
    body = (
        f"{ids_values('t', 'catalog_table', table_ids)} "
        f"?t {P.catalog_table.ref_schema_id} ?sch . "
        f"?sch {P.catalog_schema.name} ?schema_name ; {P.catalog_schema.ref_database_id} ?db . "
        f"?db {P.catalog_database.name} ?database_name"
    )
    names = {
        row_id(r["t"]): (r["schema_name"], r["database_name"])
        for r in client().select(f"SELECT ?t ?schema_name ?database_name WHERE {{ {wrap(body)} }}")
    }
    tables: dict[str, dict[str, Any]] = {}
    for tid, row in rows.items():
        if tid not in names:
            continue
        payload = dict(row)
        payload["schema_name"], payload["database_name"] = names[tid]
        payload["label"] = Labels.TABLE
        payload["columns"] = []
        tables[tid] = payload

    body = (
        f"{ids_values('t', 'catalog_table', table_ids)} "
        f"?c a {iri(class_iri('catalog_column'))} ; {P.catalog_column.ref_table_id} ?t ; "
        f"   {P.catalog_column.name} ?name . "
        f"OPTIONAL {{ ?c {P.catalog_column.ordinal_position} ?pos }} "
        f"OPTIONAL {{ ?c {P.catalog_column.data_type} ?data_type }} "
        f"OPTIONAL {{ ?c {P.catalog_column.sample_values} ?sample_values }} "
        f"OPTIONAL {{ ?c {P.catalog_column.format} ?format }}"
    )
    crows = client().select(
        f"SELECT ?c ?t ?name ?pos ?data_type ?sample_values ?format WHERE {{ {wrap(body)} }}"
    )
    descs = column_descriptions({row_id(r["c"]) for r in crows})
    # ORDER BY table_id, ordinal_position (NULLs last, as Postgres sorts ASC).
    crows.sort(
        key=lambda r: (row_id(r["t"]), r.get("pos") is None, r.get("pos") or 0, row_id(r["c"]))
    )
    for r in crows:
        table = tables.get(row_id(r["t"]))
        if table is None:
            continue
        sample_values = stringify_sample_values(r.get("sample_values"))
        table["columns"].append(
            {
                "name": r["name"],
                "data_type": str(r.get("data_type") or ""),
                "description": descs.get(row_id(r["c"])),
                "format": r.get("format"),
                "sample_values": sample_values if sample_values else None,
            }
        )
    return tables


def _relevant(owned: dict[str, list[str]], tables, owner_id: str) -> list[dict[str, Any]]:
    return [tables[t] for t in owned.get(owner_id, []) if t in tables]


def _expand_custom_analyses(ids: list[str]) -> dict[str, dict[str, Any]]:
    rows = read_rows("custom_analysis", ids)
    sql = statement_texts("custom_analysis__sql", "analysis_id", ids)
    owned = owned_table_ids("custom_analysis__sql", "analysis_id", ids)
    tables = _table_payloads(sorted({t for v in owned.values() for t in v}))
    return {
        rid: {**row, "sql": sql.get(rid, ""), "relevant_tables": _relevant(owned, tables, rid)}
        for rid, row in rows.items()
    }


def _expand_sql_attributes(ids: list[str]) -> dict[str, dict[str, Any]]:
    """Attributes with a Term and a statement only; the lowest-id Term wins."""
    rows = read_rows("sql_attribute", ids)
    body = (
        f"{ids_values('a', 'sql_attribute', ids)} "
        f"?l {P.sql_attribute__term.ref_attribute_id} ?a ; {P.sql_attribute__term.ref_term_id} ?term . "
        f"?term {P.term.name} ?term_name"
    )
    terms: dict[str, tuple[str, str]] = {}
    for r in client().select(f"SELECT ?a ?term ?term_name WHERE {{ {wrap(body)} }}"):
        a, t = row_id(r["a"]), row_id(r["term"])
        if a not in terms or t < terms[a][0]:
            terms[a] = (t, r["term_name"])
    sql = statement_texts("sql_attribute__sql", "attribute_id", ids)
    owned = owned_table_ids("sql_attribute__sql", "attribute_id", ids)
    tables = _table_payloads(sorted({t for v in owned.values() for t in v}))

    result: dict[str, dict[str, Any]] = {}
    for rid in sorted(rows):
        if rid not in terms or rid not in sql:  # inner joins: needs a Term and a statement
            continue
        result[rid] = {
            **rows[rid],
            "sql": sql[rid],
            "relevant_tables": _relevant(owned, tables, rid),
            "term_name": terms[rid][1],
            "term_id": terms[rid][0],
        }
    return result


def _expand_columns(ids: list[str]) -> dict[str, dict[str, Any]]:
    rows = read_rows("catalog_column", ids)
    parents = read_rows("catalog_table", sorted({r["table_id"] for r in rows.values() if r["table_id"]}))
    tables = _table_payloads(sorted(parents))

    result: dict[str, dict[str, Any]] = {}
    for rid, row in rows.items():
        parent_row = parents.get(row["table_id"])
        if parent_row is None:  # inner join to catalog_table
            continue
        payload = dict(row)
        payload["table_name"] = parent_row["name"]
        payload["table_type"] = parent_row["table_type"]
        payload["parent_id"] = parent_row["id"]
        payload["sample_values"] = stringify_sample_values(row["sample_values"])
        parent = tables.get(row["table_id"])
        result[rid] = {**payload, "relevant_tables": [parent] if parent else []}
    return result


def _expand_plain(table: str):
    def expand(ids: list[str]) -> dict[str, dict[str, Any]]:
        return {rid: dict(row) for rid, row in read_rows(table, ids).items()}

    return expand


_EXPANDERS = {
    Labels.CUSTOM_ANALYSIS: _expand_custom_analyses,
    "SqlAttribute": _expand_sql_attributes,
    Labels.COLUMN: _expand_columns,
}


def expand_info(ids_and_labels: list | None) -> dict:
    by_label: dict[str, list[str]] = {}
    for item in ids_and_labels or []:
        if not isinstance(item, dict) or item.get("id") is None:
            continue
        label = str(item.get("label") or "").strip()
        if not label:
            continue
        by_label.setdefault(label, []).append(item["id"])

    results: dict[str, Any] = {}
    allowed = set(Labels.LIST_OF_ALL) | set(_EXPANDERS)
    for label, ids in by_label.items():
        if label not in allowed:
            logger.warning("Skipping unknown label %r in expand_info", label)
            continue
        expander = _EXPANDERS.get(label)
        if expander is None:
            table = _NODE_TABLES.get(label)
            if table is None:
                logger.warning("No table for label %r in expand_info", label)
                continue
            expander = _expand_plain(table)
        results |= expander(sorted(set(ids)))
    return results
