# SPDX-License-Identifier: Apache-2.0
"""RDF port of ``auto_ontology.dal.session.write_transaction``."""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

from auto_ontology.rdf import writes

logger = logging.getLogger(__name__)

TARGET = "auto_ontology.dal.session"
PORTS = ["write_transaction"]

_logged = False


@contextmanager
def write_transaction() -> Iterator[None]:
    """The block's writes as one unit, as far as this backend can make them one.

    ``NIDRA_WRITES=commit``: the block's ops are collected and sent as ONE commit
    request at the end (one ledger frame, atomic by construction); an exception
    discards them, which is the rollback. A read inside the block first sends what
    is pending (a ledger transaction's frames are invisible to reads until its
    commit lands), so a span that reads its own writes is split at that read.
    Upstream's spans are DAL-only sequences of a few requests.

    ``NIDRA_WRITES=sparql``: gardend applies each SPARQL update request atomically,
    but a sequence of requests cannot be grouped, so an exception in the block does
    NOT roll back what the block already wrote. Logged once, at debug level.
    """
    if writes.write_mode() == "commit":
        with writes.collecting():
            yield
        return
    global _logged
    if not _logged:
        _logged = True
        logger.debug("write_transaction: RDF backend has no multi-request transaction; writes are not rolled back")
    yield
