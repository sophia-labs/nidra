# SPDX-License-Identifier: Apache-2.0
"""RDF ports of ``auto_ontology.dal.tags`` reads."""
from __future__ import annotations

from typing import Any

from auto_ontology.dal_rdf._common import P, client, iri, values, wrap
from auto_ontology.rdf.vocab import ref_iri, row_id, row_iri_single

PORTS = ["fetch_tags_map"]

#: tag_target column -> the table its reference points at.
_TARGET_TABLE = {
    "term_id": "term",
    "table_id": "catalog_table",
    "column_id": "catalog_column",
    "column_attribute_id": "column_attribute",
    "sql_attribute_id": "sql_attribute",
}


def fetch_tags_map(kind: str, item_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    from auto_ontology.dal import tags as dal_tags

    column = dal_tags._target_column(kind).name  # raises ValueError for an unknown kind
    if not item_ids:
        return {}
    table = _TARGET_TABLE[column]
    items = [row_iri_single(table, i) for i in item_ids]
    rows = client().select(
        "SELECT ?tt ?item ?tag ?name WHERE { "
        + wrap(
            f"{values('item', items)} "
            f"?tt {iri(ref_iri('tag_target', column))} ?item ; {P.tag_target.ref_tag_id} ?tag . "
            f"?tag {P.tag.name} ?name"
        )
        + " }"
    )
    rows.sort(key=lambda r: (r["name"].lower(), row_id(r["tag"])))
    tags: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        tags.setdefault(row_id(r["item"]), []).append({"id": row_id(r["tag"]), "name": r["name"]})
    return tags
