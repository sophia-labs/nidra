# SPDX-License-Identifier: Apache-2.0
"""RDF ports of the ``auto_ontology.dal.connections`` writes.

The ``connection`` column is JSONB: it is stored as the ``rdf:JSON`` literal
(``lit`` of the decoded dict), the same form the export and the reads use.
"""
from __future__ import annotations

import json
from typing import Any

from auto_ontology.dal_rdf._common import P, class_iri, client, iri, lit, row_id, wrap
from auto_ontology.dal_rdf._wdata import apply_cells
from auto_ontology.dal_rdf.datasources__b import fetch_item_by_id as _fetch_item_by_id
from auto_ontology.rdf import writes as W

PORTS = ["insert_connection", "clear_connection"]


def _database_by_name(database_name: str) -> str | None:
    rows = client().select(
        "SELECT ?db WHERE { "
        + wrap(f"?db a {iri(class_iri('catalog_database'))} ; {P.catalog_database.name} {lit(database_name)} .")
        + " } LIMIT 1"
    )
    return rows[0]["db"] if rows else None


def insert_connection(*, connection: str, database_name: str) -> dict[str, Any]:
    """Attach connection metadata to the catalog database, creating it if needed."""
    payload = json.loads(connection) if connection and connection.strip() else None
    subject = _database_by_name(database_name)
    if subject is None:
        stored = W.insert_row("catalog_database", {"name": database_name, "connection": payload})
        database_id = stored["id"]
    else:
        database_id = row_id(subject)
        apply_cells({"catalog_database": {subject: {"connection": payload}}})
    row = _fetch_item_by_id(database_id, "Database")
    row.pop("label", None)
    return row


def clear_connection(*, database_name: str) -> None:
    """Detach connection metadata from the catalog database (the row stays)."""
    subject = _database_by_name(database_name)
    if subject is not None:
        apply_cells({"catalog_database": {subject: {"connection": None}}})
