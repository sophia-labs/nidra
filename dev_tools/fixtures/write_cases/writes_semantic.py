# SPDX-License-Identifier: Apache-2.0
"""write_diff drivers for the semantic write ports.

``run`` calls a DAL write (a ``module:function`` string) and returns what it
returned, the exception class it raised (if any), and a full dump of the named
tables as the backend stores them (Postgres: ``SELECT *``; gardend: the
Direct-Mapping triples read back column by column). The dump is what makes a
SAME mean "the rows are identical", beyond what the DAL reads happen to expose.
Timestamp columns are left out (they are the clock).
"""
from __future__ import annotations

import importlib
import json
import os
import re
from typing import Any

from sqlalchemy import ARRAY, JSON, DateTime, select


def _dump_pg(table_name: str) -> list[dict[str, Any]]:
    from auto_ontology.dal import schema as s
    from auto_ontology.dal.session import store

    table = s.METADATA.tables[table_name]
    cols = [c for c in table.columns if not isinstance(c.type, DateTime)]
    return [dict(r) for r in store().query_read(select(*cols))]


def _dump_rdf(table_name: str) -> list[dict[str, Any]]:
    from auto_ontology.dal import schema as s
    from auto_ontology.rdf.client import client, iri, wrap
    from auto_ontology.rdf.vocab import class_iri, prop_iri

    table = s.METADATA.tables[table_name]
    cols = [c for c in table.columns if not isinstance(c.type, DateTime)]
    opts = []
    for i, c in enumerate(cols):
        flt = ""
        if isinstance(c.type, (ARRAY, JSON)):
            flt = ' FILTER(datatype(?c%d) = <http://www.w3.org/1999/02/22-rdf-syntax-ns#JSON>)' % i
        opts.append(f"OPTIONAL {{ ?s {iri(prop_iri(table_name, c.name))} ?c{i} .{flt} }}")
    body = f"?s a {iri(class_iri(table_name))} . {' '.join(opts)}"
    sel = " ".join(f"?c{i}" for i in range(len(cols)))
    rows = client().select(f"SELECT ?s {sel} WHERE {{ {wrap(body)} }}")
    return [{c.name: r.get(f"c{i}") for i, c in enumerate(cols)} for r in rows]


_DEBRIS = re.compile(r"^x-[0-9a-f]{8}-")


def _debris(row: dict[str, Any]) -> bool:
    """Rows left in the Postgres fixture database by test suites (``x-<8 hex>-...``);
    they are not in the graph copy, so they are not compared."""
    return any(isinstance(v, str) and _DEBRIS.match(v) for v in row.values())


def _freeze_modified() -> dict[str, list[bool]]:
    """``modified`` is the clock (PG ``now()`` vs the port's own): record, per table, which
    rows carry one (``{table: sorted [True/False]}``) and set it to a constant, so the DAL
    reads that follow compare the rest. Checks the *touch* semantics without the clock."""
    from datetime import datetime, timezone

    from auto_ontology.dal import schema as s

    const = datetime(2000, 1, 1, tzinfo=timezone.utc)
    seen: dict[str, list[bool]] = {}
    rdf = os.environ.get("CATALOG_READ_BACKEND", "postgres").lower() == "rdf"
    for table in s.METADATA.sorted_tables:
        if "modified" not in table.columns:
            continue
        if rdf:
            from auto_ontology.rdf import writes as W
            from auto_ontology.rdf.client import client, iri, wrap
            from auto_ontology.rdf.vocab import class_iri, prop_iri

            body = (
                f"?s a {iri(class_iri(table.name))} . "
                f"OPTIONAL {{ ?s {iri(prop_iri(table.name, 'modified'))} ?m }}"
            )
            rows = client().select(f"SELECT ?s ?m WHERE {{ {wrap(body)} }}")
            with_m = [r["s"] for r in rows if r.get("m") is not None]
            seen[table.name] = sorted(r.get("m") is not None for r in rows)
            if with_m:
                W.update_rows(table.name, with_m, {"modified": const})
        else:
            from sqlalchemy import update

            from auto_ontology.dal.session import store

            rows = store().query_read(select(table.c.modified))
            seen[table.name] = sorted(r["modified"] is not None for r in rows)
            store().query_write(update(table).where(table.c.modified.is_not(None)).values(modified=const))
    return seen


def run(
    call: str,
    kwargs: dict[str, Any] | None = None,
    tables: list[str] | None = None,
    before: list[list[Any]] | None = None,
) -> dict[str, Any]:
    """``before``: ``[[call, kwargs], ...]`` run first (state to act on), on both backends."""
    for step_call, step_kwargs in before or []:
        step_module, step_fn = step_call.split(":")
        getattr(importlib.import_module(step_module), step_fn)(**step_kwargs)
    module_name, fn_name = call.split(":")
    fn = getattr(importlib.import_module(module_name), fn_name)
    out: dict[str, Any] = {}
    try:
        out["returned"] = fn(**(kwargs or {}))
    except Exception as exc:  # noqa: BLE001
        out["raised"] = type(exc).__name__
    rdf = os.environ.get("CATALOG_READ_BACKEND", "postgres").lower() == "rdf"
    out["modified_present"] = {k: v for k, v in _freeze_modified().items() if True in v}
    dump = _dump_rdf if rdf else _dump_pg
    if tables == ["*"]:
        from auto_ontology.dal import schema as s

        tables = [tb.name for tb in s.METADATA.sorted_tables]
    for t in tables or []:
        out["table:" + t] = sorted(
            (_plain(r) for r in dump(t) if not _debris(r)), key=lambda r: json.dumps(r, sort_keys=True, default=str)
        )
    return out


def _plain(row: dict[str, Any]) -> dict[str, Any]:
    return {k: (list(v) if isinstance(v, (list, tuple)) else v) for k, v in row.items()}
