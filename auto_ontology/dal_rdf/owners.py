# SPDX-License-Identifier: Apache-2.0
"""RDF port of ``auto_ontology.dal.owners``: which row an edit is about, and
the owner stored on it. Same signatures and answers; the tables are the
SQLAlchemy ``Table`` objects the callers pass, and only their names are used."""
from __future__ import annotations

from typing import Any

from auto_ontology.dal import schema as s
from auto_ontology.dal.owners import CATALOG_EDIT, EditSubject, _MISSING  # noqa: F401
from auto_ontology.dal_rdf._common import P, client, iri, wrap
from auto_ontology.dal_rdf import _common
from auto_ontology.rdf import writes as W
from auto_ontology.rdf.vocab import class_iri, row_iri_single

PORTS = ["edit_subject", "current_owner", "assign_owner", "_stored_owner", "_row_exists"]

# The tables, in the order the upstream loops try them.
_OWNED = (s.catalog_database, s.catalog_schema, s.catalog_table, s.term)
_EDITABLE = (s.catalog_column, s.column_attribute, s.sql_attribute)


def _name(table: Any) -> str:
    return table if isinstance(table, str) else table.name


def _stored_owner(table, entity_id: str):
    name = _name(table)
    subject = iri(row_iri_single(name, entity_id))
    rows = client().select(
        "SELECT ?o WHERE { "
        + wrap(f"{subject} a {iri(class_iri(name))} . OPTIONAL {{ {subject} {P.__getattr__(name).owner_id} ?o }}")
        + " } LIMIT 1"
    )
    if not rows:
        return _MISSING
    return rows[0].get("o")


def _row_exists(table, entity_id: str) -> bool:
    name = _name(table)
    subject = iri(row_iri_single(name, entity_id))
    rows = client().select(f"SELECT ?s WHERE {{ {wrap(f'{subject} a {iri(class_iri(name))} . BIND({subject} AS ?s)')} }} LIMIT 1")
    return bool(rows)


def edit_subject(entity_id: str):
    for table in _OWNED:
        stored = _stored_owner(table, entity_id)
        if stored is not _MISSING:
            return EditSubject(permission=CATALOG_EDIT, owner_id=stored)
    for table in _EDITABLE:
        if _row_exists(table, entity_id):
            return EditSubject(permission=CATALOG_EDIT, owner_id=None)
    return None


def current_owner(table, entity_id: str):
    stored = _stored_owner(table, entity_id)
    return None if stored is _MISSING else stored


def assign_owner(table, entity_id: str, owner_id) -> None:
    W.update_row(_name(table), entity_id, {"owner_id": owner_id})
