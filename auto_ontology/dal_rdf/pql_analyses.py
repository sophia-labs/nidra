# SPDX-License-Identifier: Apache-2.0
"""RDF ports of ``auto_ontology.dal.pql_analyses`` reads."""
from __future__ import annotations

import logging

from auto_ontology.dal_rdf.sql_attributes import read_rows

logger = logging.getLogger(__name__)

PORTS = ["fetch_pql_analyses_by_ids"]


def fetch_pql_analyses_by_ids(analysis_ids: list[str]) -> dict[str, dict[str, str]]:
    if not analysis_ids:
        return {}
    try:
        rows = read_rows("pql_analysis", list(analysis_ids))
    except Exception:
        logger.warning("fetch_pql_analyses_by_ids: query failed", exc_info=True)
        return {}

    return {
        row["id"]: {
            "id": row["id"],
            "name": (row["name"] or "").strip(),
            "description": (row["description"] or "").strip(),
            "pql": (row["pql"] or "").strip(),
        }
        for row in rows.values()
        if row["id"]
    }
