# SPDX-License-Identifier: Apache-2.0
"""Semantic compilation history on gardend (``semantic_compilation_history``)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from auto_ontology.dal_rdf._common import P, client, wrap
from auto_ontology.rdf.vocab import row_id, row_iri_single
from auto_ontology.dal.schema import RUN_FAILED, RUN_SUCCEEDED
from auto_ontology.rdf.writes import insert_row, update_row

logger = logging.getLogger(__name__)

TARGET = "auto_ontology.ingestion_service.history"
PORTS = ["record_run_start", "record_run_finish", "get_last_successful_run"]
_TABLE = "semantic_compilation_history"


def _next_id() -> int:
    rows = client().select(f"SELECT (MAX(?id) AS ?m) WHERE {{ {wrap(f'?s {P.semantic_compilation_history.id} ?id .')} }}")
    current = rows[0].get("m") if rows else None
    return int(current or 0) + 1


def record_run_start() -> int | None:
    try:
        run_id = _next_id()
        insert_row(_TABLE, {"id": run_id, "started_at": datetime.now(timezone.utc)})
        return run_id
    except Exception:  # noqa: BLE001
        logger.exception("record_run_start failed")
        return None


def record_run_finish(run_id: int | None, outcome: str | None) -> None:
    if run_id is None or outcome is None:
        return
    if outcome not in (RUN_SUCCEEDED, RUN_FAILED):
        raise ValueError(f"invalid semantic compilation run outcome: {outcome!r}")
    try:
        update_row(_TABLE, str(run_id), {"finished_at": datetime.now(timezone.utc), "status": outcome})
    except Exception:  # noqa: BLE001
        logger.exception("record_run_finish failed")


def get_last_successful_run() -> str | None:
    try:
        rows = client().select(
            f"SELECT ?f WHERE {{ {wrap(f'?s {P.semantic_compilation_history.finished_at} ?f ; {P.semantic_compilation_history.status} ?o . FILTER(?o = \"{RUN_SUCCEEDED}\")')} }} ORDER BY DESC(?f) LIMIT 1"
        )
        return rows[0]["f"].isoformat() if rows else None
    except Exception:  # noqa: BLE001
        logger.exception("get_last_successful_run failed")
        return None
