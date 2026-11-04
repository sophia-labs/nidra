# SPDX-License-Identifier: Apache-2.0
"""RDF ports of ``auto_ontology.dal.custom_analyses`` reads."""
from __future__ import annotations

import logging
from typing import Any

from auto_ontology.dal_rdf._common import P, client, iri, wrap
from auto_ontology.dal_rdf.sql_attributes import ids_values, read_rows, statement_texts, tables_from_statements
from auto_ontology.rdf.vocab import class_iri

logger = logging.getLogger(__name__)

PORTS = [
    "fetch_custom_analyses",
    "fetch_custom_analyses_with_sql",
    "fetch_tables_from_custom_analyses",
    "custom_analysis_exists",
]


def fetch_custom_analyses() -> list[dict[str, str]]:
    try:
        rows = read_rows("custom_analysis")
        texts = statement_texts("custom_analysis__sql", "analysis_id")
        ordered = sorted(
            (r for r in rows.values() if r["id"] in texts),  # _has_sql
            key=lambda r: r["name"],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to fetch custom analyses: %s", exc)
        return []

    rules: list[dict[str, str]] = []
    for row in ordered:
        if not row["name"]:
            continue
        parts = []
        if row["description"]:
            parts.append(row["description"])
        sql_code = texts.get(row["id"])
        if sql_code:
            parts.append(f"SQL: {sql_code}")
        if parts:
            rules.append({"name": row["name"], "description": "\n".join(parts)})
    logger.info("Fetched %d custom analyses as domain rules", len(rules))
    return rules


def fetch_custom_analyses_with_sql(analysis_ids: list[str]) -> list[dict[str, str]]:
    if not analysis_ids:
        return []
    try:
        ids = list(analysis_ids)
        rows = read_rows("custom_analysis", ids)
        texts = statement_texts("custom_analysis__sql", "analysis_id", ids)
    except Exception:
        logger.warning("fetch_custom_analyses_with_sql: query failed", exc_info=True)
        return []

    return [
        {
            "id": row["id"] or "",
            "name": (row["name"] or "").strip(),
            "description": (row["description"] or "").strip(),
            "sql": (texts.get(row["id"]) or "").strip(),
        }
        for _, row in sorted(rows.items())
    ]


def fetch_tables_from_custom_analyses(analysis_ids: list[str]) -> list[dict[str, Any]]:
    if not analysis_ids:
        return []
    try:
        return tables_from_statements("custom_analysis__sql", "analysis_id", list(analysis_ids))
    except Exception:
        logger.warning("fetch_tables_from_custom_analyses: query failed", exc_info=True)
        return []


def custom_analysis_exists(database_name: str | None = None) -> bool:
    """Fails open (``True``) when the probe fails, as upstream does."""
    from auto_ontology.rdf.client import lit

    body = f"?a a {iri(class_iri('custom_analysis'))}"
    if database_name:
        body += (
            f" . ?l a {iri(class_iri('custom_analysis__sql'))} ; "
            f"{P.custom_analysis__sql.ref_analysis_id} ?a ; "
            f"{P.custom_analysis__sql.ref_sql_query_id} ?q . "
            f"?qt a {iri(class_iri('sql_query__table'))} ; "
            f"{P.sql_query__table.ref_sql_query_id} ?q ; {P.sql_query__table.ref_table_id} ?t . "
            f"?t {P.catalog_table.ref_schema_id} ?sch . "
            f"?sch {P.catalog_schema.ref_database_id} ?db . "
            f"?db {P.catalog_database.name} {lit(database_name)}"
        )
    try:
        return bool(client().select(f"SELECT ?a WHERE {{ {wrap(body)} }} LIMIT 1"))
    except Exception:
        logger.warning("custom_analysis_exists: query failed", exc_info=True)
        return True
