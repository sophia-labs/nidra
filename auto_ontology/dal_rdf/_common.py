# SPDX-License-Identifier: Apache-2.0
"""Helpers shared by the RDF ports: row lookups by IRI, property reads, ids."""
from __future__ import annotations

from typing import Any

from auto_ontology.rdf.client import client, iri, lit, values, wrap
from auto_ontology.rdf.vocab import class_iri, prop_iri, ref_iri, row_id, row_iri_single

__all__ = ["client", "iri", "lit", "values", "wrap", "class_iri", "prop_iri", "ref_iri", "row_id", "row_iri_single", "select_rows", "P"]


class _Props:
    """``P.catalog_table.name`` → the property IRI in angle brackets."""

    def __getattr__(self, table: str):
        class _T:
            def __getattr__(self_, column: str) -> str:
                if column.startswith("ref_"):
                    return iri(ref_iri(table, column[4:]))
                return iri(prop_iri(table, column))

        return _T()


P = _Props()


def select_rows(table: str, columns: list[str], extra: str = "", limit: int | None = None) -> list[dict[str, Any]]:
    """All rows of ``table`` with the given literal columns (NULLs come back absent)."""
    opt = " ".join(f"OPTIONAL {{ ?s {iri(prop_iri(table, c))} ?{c} }}" for c in columns)
    body = f"?s a {iri(class_iri(table))} . {opt} {extra}"
    sparql = f"SELECT ?s {' '.join('?' + c for c in columns)} WHERE {{ {wrap(body)} }}"
    if limit:
        sparql += f" LIMIT {limit}"
    rows = client().select(sparql)
    for r in rows:
        r["id"] = row_id(r.pop("s"))
    return rows
