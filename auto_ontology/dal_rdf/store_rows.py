# SPDX-License-Identifier: Apache-2.0
"""RDF port of ``auto_ontology.catalog.store.rows``: find-or-create by natural key.

Besides the two public functions this module holds the machinery every store
port shares (the file is not a port of anything else, so they are not in
``PORTS``):

* :func:`coerce_row` -- Postgres coerces a value to its column's type on the way
  in (``ordinal_position`` arrives as ``"3"``); a triple store keeps what it is
  given, so values are coerced the same way before they become literals.
* :class:`Upserter` -- the sequential upsert of ``upsert_row`` run against an
  in-memory overlay, so a whole batch of upserts is *one* update request: reads
  are batched (ids and natural keys through ``VALUES``), a child of a parent that
  this batch created is known to be new without asking, and nothing is written
  until :meth:`Upserter.flush`.
* :func:`commit` -- inserts plus column updates, in one SPARQL update request.

A candidate for ``auto_ontology/rdf/writes.py``: ``Upserter`` (generic upsert by
natural key), ``commit`` (batched mixed update) and ``coerce_row``.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import ARRAY, Boolean, DateTime, Integer
from sqlalchemy.exc import IntegrityError

from auto_ontology.catalog.constants import Labels
from auto_ontology.catalog.store import registry
from auto_ontology.dal.schema import METADATA
from auto_ontology.dal_rdf._common import client, iri, lit, prop_iri, class_iri, ref_iri, values, wrap
from auto_ontology.rdf.vocab import row_iri, row_iri_single
from auto_ontology.rdf.writes import _column_props, apply_defaults, data_graph_iri, row_triples

logger = logging.getLogger(__name__)

TARGET = "auto_ontology.catalog.store.rows"
PORTS = ["resolve_id", "upsert_row"]

_CHUNK = 400
_TRUE = {"t", "true", "y", "yes", "on", "1"}
_FALSE = {"f", "false", "n", "no", "off", "0"}


# -- values ---------------------------------------------------------------------


def coerce_value(column, value: Any) -> Any:
    """``value`` as Postgres would store it in ``column`` (``None`` stays)."""
    if value is None:
        return None
    t = column.type
    if isinstance(t, Boolean):
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value).strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
        raise ValueError(f'invalid input syntax for type boolean: "{value}"')
    if isinstance(t, Integer):
        return value if isinstance(value, int) and not isinstance(value, bool) else int(value)
    if isinstance(t, DateTime):
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value))
    if isinstance(t, ARRAY):
        if isinstance(value, str):  # a Postgres server default such as '{}'
            return [] if value.strip() == "{}" else [v.strip('"') for v in value.strip("{}").split(",")]
        return [v if isinstance(v, str) else str(v) for v in value]
    if t.__class__.__name__ in ("JSONB", "JSON"):
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def coerce_row(table_name: str, row: dict[str, Any]) -> dict[str, Any]:
    table = METADATA.tables[table_name]
    out = {}
    for key, value in row.items():
        column = table.columns.get(key)
        out[key] = coerce_value(column, value) if column is not None else value
    return out


def new_row(table_name: str, values_in: dict[str, Any]) -> dict[str, Any]:
    """A complete row to insert: coerced, server defaults applied, NOT NULL checked."""
    row = coerce_row(table_name, apply_defaults(table_name, coerce_row(table_name, values_in)))
    _check_not_null(table_name, row)
    return row


def _check_not_null(table_name: str, row: dict[str, Any]) -> None:
    for column in METADATA.tables[table_name].columns:
        if row.get(column.name) is None and not column.nullable and column.server_default is None:
            raise IntegrityError(
                f"INSERT INTO {table_name}",
                {},
                Exception(f'null value in column "{column.name}" of relation "{table_name}" violates not-null constraint'),
            )


# -- reads ----------------------------------------------------------------------


def _chunks(items: list, n: int = _CHUNK):
    for i in range(0, len(items), n):
        yield items[i : i + n]


def existing_ids(table_name: str, ids: Iterable[str]) -> set[str]:
    """Which of the single-key ``ids`` name a row of ``table_name``."""
    ids = list(dict.fromkeys(ids))
    found: set[str] = set()
    for chunk in _chunks(ids):
        subjects = [row_iri_single(table_name, i) for i in chunk]
        rows = client().select(
            f"SELECT ?s WHERE {{ {wrap(f'{values(chr(115), subjects)} ?s a {iri(class_iri(table_name))} .')} }}"
        )
        found.update(r["s"] for r in rows)
    return {i for i in ids if row_iri_single(table_name, i) in found}


def existing_subjects(table_name: str, subjects: Iterable[str]) -> set[str]:
    """Which of the row IRIs exist."""
    subjects = list(dict.fromkeys(subjects))
    found: set[str] = set()
    for chunk in _chunks(subjects):
        rows = client().select(
            f"SELECT ?s WHERE {{ {wrap(f'{values(chr(115), chunk)} ?s a {iri(class_iri(table_name))} .')} }}"
        )
        found.update(r["s"] for r in rows)
    return found


def _key_parts(spec: registry.EntitySpec) -> list[str]:
    return [spec.parent_column if p == "parent" else p for p in spec.natural_key]


def find_by_key(spec: registry.EntitySpec, keys: list[tuple]) -> dict[tuple, str]:
    """``{key: id}`` for the natural keys that match a stored row.

    A key is one value per natural-key part (the parent's id for ``"parent"``);
    a ``None`` part matches a row where the column is absent, like ``IS NULL``.
    """
    table = spec.table.name
    parts = _key_parts(spec)
    found: dict[tuple, str] = {}
    plain = [k for k in dict.fromkeys(keys) if all(v is not None for v in k)]
    for chunk in _chunks(plain):
        names = [f"?k{i}" for i in range(len(parts))]
        patterns = " ".join(f"?s {iri(prop_iri(table, c))} {n} ." for c, n in zip(parts, names))
        rows_values = " ".join("(" + " ".join(lit(v) for v in k) + ")" for k in chunk)
        body = f"VALUES ({' '.join(names)}) {{ {rows_values} }} ?s a {iri(class_iri(table))} . {patterns}"
        sparql = f"SELECT ?s {' '.join(names)} WHERE {{ {wrap(body)} }} ORDER BY ?s"
        for r in client().select(sparql):
            found.setdefault(tuple(r[n[1:]] for n in names), _id_of(r["s"]))
    for key in dict.fromkeys(k for k in keys if k not in plain):
        pats, filters = [], []
        for i, (c, v) in enumerate(zip(parts, key)):
            if v is None:
                filters.append(f"FILTER NOT EXISTS {{ ?s {iri(prop_iri(table, c))} ?x{i} }}")
            else:
                pats.append(f"?s {iri(prop_iri(table, c))} {lit(v)} .")
        body = f"?s a {iri(class_iri(table))} . {' '.join(pats)} {' '.join(filters)}"
        rows = client().select(f"SELECT ?s WHERE {{ {wrap(body)} }} ORDER BY ?s LIMIT 1")
        if rows:
            found[key] = _id_of(rows[0]["s"])
    return found


def _id_of(subject: str) -> str:
    from auto_ontology.rdf.vocab import row_id

    return row_id(subject)


# -- the batched writer ---------------------------------------------------------


def commit(
    inserts: dict[str, list[dict[str, Any]]] | None = None,
    updates: Iterable[tuple[str, str, dict[str, Any]]] = (),
) -> None:
    """Apply inserts and column updates in ONE SPARQL update request.

    ``inserts``: ``{table: [row, ...]}`` of complete rows (defaults applied).
    ``updates``: ``(table, subject_iri, {column: value})``; ``None`` clears the
    column. An update of a subject that does not exist is dropped, as an
    ``UPDATE`` that matches no row is. The request is a DELETE of the replaced
    triples followed (``;``) by one INSERT DATA of the new ones; gardend applies
    the request atomically.
    """
    g = iri(data_graph_iri())
    lines: list[str] = []
    for table, rows in (inserts or {}).items():
        for row in rows:
            lines.extend(row_triples(table, row))

    by_table: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for table, subject, changes in updates:
        if changes:
            by_table.setdefault(table, []).append((subject, changes))
    pairs: list[str] = []
    from auto_ontology.rdf.vocab import parse_row_iri

    for table, items in by_table.items():
        alive = existing_subjects(table, [s for s, _ in items])
        merged: dict[str, dict[str, Any]] = {}
        for subject, changes in items:
            if subject in alive:
                merged.setdefault(subject, {}).update(coerce_row(table, changes))
        for subject, changes in merged.items():
            for column in changes:
                for prop in _column_props(table, column):
                    pairs.append(f"({iri(subject)} {iri(prop)})")
            _, pk = parse_row_iri(subject)
            kept = [c for c, v in changes.items() if v is not None]
            if kept:
                lines.extend(row_triples(table, {**pk, **{c: changes[c] for c in kept}}, columns=kept))

    ops = []
    if pairs:
        ops.append(
            f"DELETE {{ GRAPH {g} {{ ?s ?p ?o }} }} WHERE {{ GRAPH {g} {{ VALUES (?s ?p) {{ {' '.join(pairs)} }} ?s ?p ?o }} }}"
        )
    if lines:
        ops.append(f"INSERT DATA {{ GRAPH {g} {{ {' '.join(lines)} }} }}")
    if ops:
        client().update(" ; ".join(ops))


class Upserter:
    """``upsert_row`` over an overlay: sequential semantics, one write request.

    Call :meth:`upsert` as many times as upstream would call ``upsert_row`` (a
    later call sees what an earlier one created or matched), then
    :meth:`flush`. :meth:`prefetch` is an optimisation only.
    """

    def __init__(self) -> None:
        self._present: dict[str, set[str]] = {}
        self._absent: dict[str, set[str]] = {}
        self._keys: dict[str, dict[tuple, str | None]] = {}
        self._new: dict[str, dict[str, dict[str, Any]]] = {}
        self._changes: dict[str, dict[str, dict[str, Any]]] = {}
        self.extra_updates: list[tuple[str, str, dict[str, Any]]] = []
        self.extra_inserts: dict[str, list[dict[str, Any]]] = {}

    # -- lookups
    def prefetch(self, label: str, match_props_list: Iterable[dict[str, Any]]) -> None:
        spec = registry.entity_spec(label)
        table = spec.table.name
        match_props_list = list(match_props_list)
        self._prefetch_keys(spec, match_props_list)
        ids = [m["id"] for m in match_props_list if m.get("id")]
        ids = [i for i in dict.fromkeys(ids) if i not in self._present.get(table, ()) and i not in self._absent.get(table, ())]
        if ids:
            have = existing_ids(table, ids)
            self._present.setdefault(table, set()).update(have)
            self._absent.setdefault(table, set()).update(i for i in ids if i not in have)

    def _prefetch_keys(self, spec: registry.EntitySpec, matches: list[dict[str, Any]]) -> None:
        """Warm the cache for parent-less natural keys (a database name, a statement)."""
        if not spec.natural_key or "parent" in spec.natural_key:
            return
        cache = self._keys.setdefault(spec.table.name, {})
        keys = [k for k in dict.fromkeys(self._key(spec, m, None) for m in matches) if k not in cache]
        found = find_by_key(spec, keys)
        for k in keys:
            cache[k] = found.get(k)

    def _has_id(self, table: str, row_id: str) -> bool:
        if row_id in self._new.get(table, ()) or row_id in self._present.get(table, ()):
            return True
        if row_id in self._absent.get(table, ()):
            return False
        have = row_id in existing_ids(table, [row_id])
        (self._present if have else self._absent).setdefault(table, set()).add(row_id)
        return have

    def _key(self, spec: registry.EntitySpec, props: dict[str, Any], parent_id: str | None) -> tuple:
        key = []
        for part in spec.natural_key:
            if part == "parent":
                if spec.parent_column is None:
                    raise ValueError(f"{spec.label} has no parent column to match on")
                key.append(parent_id)
            else:
                value = props.get(part)
                key.append(coerce_value(spec.table.columns[part], value))
        return tuple(key)

    def _by_key(self, spec: registry.EntitySpec, key: tuple) -> str | None:
        table = spec.table.name
        cache = self._keys.setdefault(table, {})
        if key in cache:
            return cache[key]
        if any(v is None for v in key):
            # `col = NULL` renders IS NULL: only a stored row lacking the column
            # matches, never one created by this batch. Not cached.
            return find_by_key(spec, [key]).get(key)
        parent_table = registry.entity_spec(spec.parent_label).table.name if spec.parent_label else None
        # A child of a parent this batch created cannot be stored yet.
        parent_is_new = any(
            part == "parent" and value is not None and value in self._new.get(parent_table, ())
            for part, value in zip(spec.natural_key, key)
        )
        found = None if parent_is_new else find_by_key(spec, [key]).get(key)
        cache[key] = found
        return found

    def resolve(self, label: str, match_props: dict[str, Any], *, parent_id: str | None = None) -> str | None:
        spec = registry.entity_spec(label)
        table = spec.table.name
        if match_props.get("id") and self._has_id(table, match_props["id"]):
            return match_props["id"]
        if "database_name" in match_props and spec.parent_label == Labels.DB:
            parent_id = self.resolve(Labels.DB, {"name": match_props["database_name"]})
            if parent_id is None:
                return None
        if not spec.natural_key:
            return None
        return self._by_key(spec, self._key(spec, match_props, parent_id))

    # -- writes
    def upsert(
        self,
        label: str,
        match_props: dict[str, Any],
        properties: dict[str, Any],
        *,
        parent_id: str | None = None,
        on_match: dict[str, Any] | None = None,
    ) -> str:
        spec = registry.entity_spec(label)
        table = spec.table.name
        values_in = registry.projected(label, properties)
        if spec.parent_column and parent_id is not None:
            values_in[spec.parent_column] = parent_id

        lookup_parent = parent_id
        existing = self.resolve(label, match_props, parent_id=parent_id)
        if existing is None:
            row = new_row(table, values_in)
            new_id = row["id"]
            self._new.setdefault(table, {})[new_id] = row
            self._present.setdefault(table, set()).add(new_id)
            cache = self._keys.setdefault(table, {})
            row_key = tuple(
                row.get(spec.parent_column) if part == "parent" else row.get(part) for part in spec.natural_key
            )
            if spec.natural_key:
                cache[row_key] = new_id
                if "database_name" in match_props and spec.parent_label == Labels.DB:
                    lookup_parent = row.get(spec.parent_column)
                lookup_key = self._key(spec, match_props, lookup_parent)
                if all(v is not None for v in lookup_key):
                    cache[lookup_key] = new_id
            return new_id

        update = registry.projected(label, dict(on_match or {}))
        update.pop("id", None)
        if spec.parent_column and parent_id is not None:
            update[spec.parent_column] = parent_id
        if update:
            self.set_columns(table, existing, update)
        return existing

    def set_columns(self, table: str, row_id: str, changes: dict[str, Any]) -> None:
        """Queue ``UPDATE table SET changes WHERE id = row_id``."""
        changes = coerce_row(table, changes)
        pending = self._new.get(table, {}).get(row_id)
        if pending is not None:
            pending.update(changes)
            return
        self._changes.setdefault(table, {}).setdefault(row_id, {}).update(changes)

    def flush(self) -> None:
        updates = [
            (table, row_iri_single(table, row_id), changes)
            for table, per_id in self._changes.items()
            for row_id, changes in per_id.items()
        ] + self.extra_updates
        inserts = {t: list(rows.values()) for t, rows in self._new.items() if rows}
        for table, rows in self.extra_inserts.items():
            inserts.setdefault(table, []).extend(rows)
        commit(inserts, updates)
        self._changes.clear()
        self.extra_updates = []
        self.extra_inserts = {}
        self._new = {}


# -- the ports ------------------------------------------------------------------


def resolve_id(label, match_props, *, parent_id=None):
    """Find an existing row's id from whatever the caller matched on."""
    return Upserter().resolve(label, match_props, parent_id=parent_id)


def upsert_row(label, match_props, properties, *, parent_id=None, on_match=None):
    """Create or update one row, returning its id (see the upstream docstring)."""
    up = Upserter()
    row_id = up.upsert(label, match_props, properties, parent_id=parent_id, on_match=on_match)
    up.flush()
    return row_id
