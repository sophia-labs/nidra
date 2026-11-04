# SPDX-License-Identifier: Apache-2.0
"""RDF ports of the ``auto_ontology.dal.custom_analyses`` writes.

Creation of a CustomAnalysis is not a DAL function: ``server/custom_analyses/service.py``
builds a ``CatalogNode`` and calls ``catalog.store.queries.add_query`` (the
catalog-store port). ``_custom_analysis_docs`` is ported too (it read Postgres),
so ``embed_custom_analyses`` works with no Postgres.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from auto_ontology.dal_rdf import _semantic_writes as sw
from auto_ontology.dal_rdf._common import select_rows
from auto_ontology.dal_rdf.sql_attributes import statement_texts
from auto_ontology.rdf import writes as W

if TYPE_CHECKING:
    from auto_ontology.utils.embedding import EmbedParams
    from auto_ontology.vdb.postgres import PostgresVDB

logger = logging.getLogger(__name__)

PORTS = [
    "detach_existing_sql_edges",
    "delete_custom_analysis_node",
    "_custom_analysis_docs",
    "embed_custom_analyses",
]


def detach_existing_sql_edges(analysis_id: str) -> None:
    """Unlink every statement from the analysis, leaving the statements alone."""
    from auto_ontology.dal_rdf._common import client

    links = sw.link_rows("custom_analysis__sql", ["sql_query_id"], {"analysis_id": analysis_id})
    op = sw.delete_link_subjects_op(r["_s"] for r in links)
    if op:
        client().update(op)


def delete_custom_analysis_node(analysis_id: str) -> None:
    """Delete the analysis **and the statements it owns**."""
    sql_ids = [
        r["sql_query_id"]
        for r in sw.link_rows("custom_analysis__sql", ["sql_query_id"], {"analysis_id": analysis_id})
    ]
    W.delete_rows("custom_analysis", [analysis_id])
    if sql_ids:
        W.delete_rows("sql_query", sql_ids)


def _custom_analysis_docs(analysis_id: str | None) -> list[dict[str, Any]]:
    """Embedding-ready docs (same text format as upstream, reproduced literally)."""
    texts = statement_texts("custom_analysis__sql", "analysis_id", [analysis_id] if analysis_id is not None else None)
    rows = select_rows("custom_analysis", ["name", "description"])
    docs: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda r: r["id"]):
        if row["id"] not in texts or (analysis_id is not None and row["id"] != analysis_id):
            continue
        description = row.get("description")
        text = f"custom_analysis: {row['name']}"
        if description is not None and str(description).strip():
            text += f", description: {description}"
        text += f", sql: {texts[row['id']]}"
        docs.append({"text": text, "name": row["name"], "label": "CustomAnalysis", "id": row["id"]})
    return docs


def embed_custom_analyses(
    embed_params: "EmbedParams",
    vdb: "PostgresVDB",
    analysis_id: str | None = None,
    database_name: str | None = None,
) -> None:
    """Embed CustomAnalysis docs and append them to *vdb*."""
    from auto_ontology.utils.embedding import embed_rows_into_vdb

    docs = _custom_analysis_docs(analysis_id)
    if not docs:
        logger.info("No CustomAnalysis rows found for analysis_id=%r; skipping VDB upsert.", analysis_id)
        return

    rows = []
    for item in docs:
        node_id = item.get("id")
        path = f"auto_ontology:{node_id}" if node_id is not None else "auto_ontology:unknown"
        tabular_fields = {
            "id": node_id,
            "label": item.get("label", ""),
            "name": item.get("name", ""),
            "source_path": path,
            "database_name": database_name,
        }
        rows.append(
            {
                "text": (item.get("text") or "").strip(),
                "path": path,
                "page_number": -1,
                "metadata": {**tabular_fields, "content_metadata": dict(tabular_fields)},
            }
        )
    embed_rows_into_vdb(rows, embed_params, vdb, what="CustomAnalysis")
