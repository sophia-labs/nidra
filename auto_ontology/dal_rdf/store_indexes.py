# SPDX-License-Identifier: Apache-2.0
"""``catalog.store.indexes.add_indices``: nothing to do on this backend.

Upstream's Postgres port is already a no-op ("indexes come from Alembic"); the
graph has no such indexes (gardend indexes every triple), so this says so
explicitly and makes the port boundary complete: the ingest path never reaches
for a Postgres connection through here."""
from __future__ import annotations

import logging

TARGET = "auto_ontology.catalog.store.indexes"
PORTS = ["add_indices"]

logger = logging.getLogger(__name__)


def add_indices():
    logger.debug("add_indices: no-op on the RDF backend")
