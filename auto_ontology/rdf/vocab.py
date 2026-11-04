# SPDX-License-Identifier: Apache-2.0
"""The IRI scheme: a W3C Direct Mapping of the catalog's SQLAlchemy metadata.

Row IRI:      urn:ao:<table>/<pk1>=<v1>;<pk2>=<v2>
Class IRI:    urn:ao:<table>
Literal prop: urn:ao:<table>#<column>
Ref prop:     urn:ao:<table>#ref-<column>   (object: the referenced row IRI)

Percent-encoding follows the Direct Mapping: unreserved characters stay, the
rest is UTF-8 percent-encoded, so a uuid or a dotted name round-trips.
"""
from __future__ import annotations

from urllib.parse import quote, unquote

BASE = "urn:ao:"
XSD = "http://www.w3.org/2001/XMLSchema#"
SKOS_PREF_LABEL = "http://www.w3.org/2004/02/skos/core#prefLabel"
DCT_DESCRIPTION = "http://purl.org/dc/terms/description"
# Columns whose value is also written as a standard label/description, so
# gardend's entity index (`find`) verbalizes every catalog row.
LABEL_COLUMNS = ("name",)
DESCRIPTION_COLUMNS = ("description",)
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"


def _enc(value: object) -> str:
    return quote(str(value), safe="-._~")


def class_iri(table: str) -> str:
    return f"{BASE}{table}"


def prop_iri(table: str, column: str) -> str:
    return f"{BASE}{table}#{column}"


def ref_iri(table: str, column: str) -> str:
    return f"{BASE}{table}#ref-{column}"


def row_iri(table: str, pk: dict[str, object]) -> str:
    """``pk`` is ``{column: value}`` in the table's primary-key order."""
    key = ";".join(f"{_enc(c)}={_enc(v)}" for c, v in pk.items())
    return f"{BASE}{table}/{key}"


def row_iri_single(table: str, value: object, column: str = "id") -> str:
    return row_iri(table, {column: value})


def parse_row_iri(iri: str) -> tuple[str, dict[str, str]]:
    """Inverse of :func:`row_iri`: ``(table, {column: value})``."""
    assert iri.startswith(BASE), iri
    table, _, key = iri[len(BASE):].partition("/")
    pk: dict[str, str] = {}
    for part in key.split(";"):
        c, _, v = part.partition("=")
        pk[unquote(c)] = unquote(v)
    return table, pk


def row_id(iri: str) -> str:
    """The ``id`` of a single-key row IRI (the common case in this catalog)."""
    _, pk = parse_row_iri(iri)
    return pk["id"]
