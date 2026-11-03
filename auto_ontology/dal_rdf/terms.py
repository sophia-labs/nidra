# SPDX-License-Identifier: Apache-2.0
"""RDF ports of ``auto_ontology.dal.terms`` reads."""
from __future__ import annotations

import logging

from auto_ontology.dal_rdf._common import P, client, values, wrap
from auto_ontology.rdf.vocab import row_iri_single

logger = logging.getLogger(__name__)

PORTS = ["fetch_term_synonyms"]


def fetch_term_synonyms(attr_ids: list[str]) -> dict[str, list[str]]:
    """``{term_name: [synonym, ...]}`` for the terms behind these attributes.

    Keyed by *name*, not id, because the caller matches synonyms against text.
    Terms with no synonyms are omitted rather than mapped to an empty list.
    """
    if not attr_ids:
        return {}
    attr_iris = [row_iri_single("column_attribute", a) for a in attr_ids]
    body = (
        f"{values('attr', attr_iris)} "
        f"?link {P.column_attribute__term.ref_attribute_id} ?attr ; "
        f"      {P.column_attribute__term.ref_term_id} ?term . "
        f"?term {P.term.name} ?name ; {P.term.synonyms} ?synonyms . "
        f"FILTER(datatype(?synonyms) = <http://www.w3.org/1999/02/22-rdf-syntax-ns#JSON>)"
    )
    try:
        rows = client().select(f"SELECT DISTINCT ?name ?synonyms WHERE {{ {wrap(body)} }}")
    except Exception:
        logger.warning("fetch_term_synonyms: query failed", exc_info=True)
        return {}
    result: dict[str, list[str]] = {}
    for row in rows:
        synonyms = [s for s in (row.get("synonyms") or []) if s]
        if row.get("name") and synonyms:
            result[row["name"]] = synonyms
    return result
