# SPDX-License-Identifier: Apache-2.0
"""RDF ports of the ``auto_ontology.dal.pql_analyses`` writes."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from auto_ontology.dal_rdf import _semantic_writes as sw
from auto_ontology.dal_rdf._common import select_rows
from auto_ontology.rdf import writes as W
from auto_ontology.semantic.constants import LABEL_PQL_ANALYSIS

if TYPE_CHECKING:
    from auto_ontology.utils.embedding import EmbedParams
    from auto_ontology.vdb.postgres import PostgresVDB

logger = logging.getLogger(__name__)

PORTS = ["upsert_pql_analysis_node", "delete_pql_analysis_node", "_pql_analysis_docs", "embed_pql_analyses"]

_LABEL = LABEL_PQL_ANALYSIS


def upsert_pql_analysis_node(analysis_id: str, name: str, description: str, pql: str) -> None:
    """Create or overwrite an analysis by id (a PUT: every field is assigned)."""
    changes = {"name": name, "description": description, "pql": pql}
    if sw.exists("pql_analysis", analysis_id):
        W.update_row("pql_analysis", analysis_id, changes)
    else:
        sw.insert_batch({"pql_analysis": [{"id": analysis_id, **changes}]})


def delete_pql_analysis_node(analysis_id: str) -> None:
    """Delete the analysis. Nothing hangs off it, so nothing cascades."""
    W.delete_rows("pql_analysis", [analysis_id])


def _pql_analysis_docs(analysis_id: str | None) -> list[dict[str, Any]]:
    """Embedding-ready docs: the question text only."""
    docs: list[dict[str, Any]] = []
    for row in sorted(select_rows("pql_analysis", ["name", "description"]), key=lambda r: r["id"]):
        if analysis_id is not None and row["id"] != analysis_id:
            continue
        description = row.get("description")
        text = row["name"]
        if description is not None and str(description).strip():
            text += f": {description}"
        docs.append({"text": text, "name": row["name"], "id": row["id"]})
    return docs


def embed_pql_analyses(
    embed_params: "EmbedParams",
    vdb: "PostgresVDB",
    analysis_id: str | None = None,
    database_name: str | None = None,
) -> None:
    """Embed PqlAnalysis docs and append them to *vdb*."""
    from auto_ontology.utils.embedding import embed_rows_into_vdb

    docs = _pql_analysis_docs(analysis_id)
    if not docs:
        logger.info("No PqlAnalysis rows found for analysis_id=%r; skipping VDB upsert.", analysis_id)
        return

    rows = []
    for item in docs:
        node_id = item.get("id")
        path = f"auto_ontology:{node_id}" if node_id is not None else "auto_ontology:unknown"
        tabular_fields = {
            "id": node_id,
            "label": _LABEL,
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
    embed_rows_into_vdb(rows, embed_params, vdb, what="PqlAnalysis")
