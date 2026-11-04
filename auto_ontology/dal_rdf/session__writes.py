# SPDX-License-Identifier: Apache-2.0
"""RDF port of ``auto_ontology.dal.session.write_transaction``."""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

logger = logging.getLogger(__name__)

TARGET = "auto_ontology.dal.session"
PORTS = ["write_transaction"]

_logged = False


@contextmanager
def write_transaction() -> Iterator[None]:
    """Run the block; there is no transaction on this backend.

    gardend applies each SPARQL update request atomically, but a sequence of
    requests cannot be grouped, so an exception in the block does NOT roll back
    what the block already wrote. The ports keep each function's writes to as
    few requests as possible (usually one) so the window is small. Logged once,
    at debug level.
    """
    global _logged
    if not _logged:
        _logged = True
        logger.debug("write_transaction: RDF backend has no multi-request transaction; writes are not rolled back")
    yield
