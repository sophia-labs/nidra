# SPDX-License-Identifier: Apache-2.0
"""RDF port of ``dal.datasources.fetch_node_properties_by_id`` (scope lane)."""
from __future__ import annotations

import logging

from auto_ontology.dal_rdf._common import class_iri, client, iri, values, wrap
from auto_ontology.rdf.vocab import prop_iri, row_iri_single

logger = logging.getLogger(__name__)

PORTS = ["fetch_node_properties_by_id"]

_TABLE_OF = {
    "Database": "catalog_database",
    "Schema": "catalog_schema",
    "Table": "catalog_table",
    "Column": "catalog_column",
}


def fetch_node_properties_by_id(id: str, label: str | list[str]) -> dict | None:
    from auto_ontology.dal import datasources as dal_ds

    labels = label if isinstance(label, list) else [label]
    for candidate in labels:
        if candidate not in _TABLE_OF:
            logger.warning("Rejecting unknown label %r in fetch_node_properties_by_id", candidate)
            return None

    for candidate in labels:
        table = _TABLE_OF[candidate]
        row = row_iri_single(table, id)
        rows = client().select(
            "SELECT ?p ?o WHERE { "
            + wrap(f"{values('s', [row])} ?s a {iri(class_iri(table))} ; ?p ?o")
            + " }"
        )
        if not rows:
            continue
        by_p: dict[str, list] = {}
        for r in rows:
            by_p.setdefault(r["p"], []).append(r["o"])
        props = {}
        for column in dal_ds._NODE_TABLES[candidate].columns:
            vals = by_p.get(prop_iri(table, column.name)) or [None]
            # ARRAY columns carry one rdf:JSON literal (a list) plus one string
            # per member; JSONB columns carry the parsed JSON. Prefer the structured one.
            structured = [v for v in vals if isinstance(v, (list, dict))]
            props[column.name] = structured[0] if structured else vals[0]
        props["label"] = candidate
        return props
    return None
