# SPDX-License-Identifier: Apache-2.0
"""RDF port of ``auto_ontology.dal.connections.list_connections``."""
from __future__ import annotations

import json
from typing import Any

from auto_ontology.connectors.vault import read_secret
from auto_ontology.dal_rdf._common import P, client, wrap

PORTS = ["list_connections"]


def list_connections() -> list[dict[str, Any]]:
    rows = client().select(
        "SELECT ?name ?connection WHERE { "
        + wrap(f"?d a <urn:ao:catalog_database> ; {P.catalog_database.name} ?name . "
               f"OPTIONAL {{ ?d {P.catalog_database.connection} ?connection }}")
        + " }"
    )
    rows.sort(key=lambda r: r["name"])
    connections: list[dict[str, Any]] = []
    for row in rows:
        secret = read_secret(str(row["name"]))
        if secret:
            connections.append(secret)
            continue
        stored = row.get("connection")
        if stored:
            connections.append(json.loads(stored) if isinstance(stored, str) else stored)
    return connections
