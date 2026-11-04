# SPDX-License-Identifier: Apache-2.0
"""write_diff drivers for the model-interchange import (apply_import_model).

    uv run --no-sync python -m dev_tools.fixtures.write_diff \
        dev_tools.fixtures.write_cases.writes_import:roundtrip_replace
"""
from __future__ import annotations

from typing import Any

import yaml


def _export_yaml() -> str:
    from auto_ontology.server.model_interchange.schemas import ExportRequest
    from auto_ontology.server.model_interchange.service import export_model

    return export_model(ExportRequest())


def _canon(text: str) -> str:
    """The export with every list put in a canonical order. Postgres does not
    order several of the export's ``DISTINCT`` reads, and an UPDATE (the
    ``imported_id`` stamp) moves rows in the heap, so two exports of the same
    catalog can list the same rows in a different order."""
    import json

    def walk(x):
        if isinstance(x, dict):
            return {k: walk(v) for k, v in x.items()}
        if isinstance(x, list):
            return sorted((walk(v) for v in x), key=lambda v: json.dumps(v, sort_keys=True, default=str))
        return x

    return json.dumps(walk(yaml.safe_load(text)), sort_keys=True, default=str)


def _upstream():
    """A private copy of upstream's module (never patched), so its Postgres
    ``apply_import_model`` can run on the Postgres copy even under the RDF backend."""
    import importlib.util
    import sys

    from auto_ontology.dal import model_interchange as patched

    name = "_upstream_model_interchange"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, patched.__file__)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


_QUERY_COUNTER = [0]


def _det_id(kind: str, key: str) -> str:
    import uuid

    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{kind}:{key}"))


def _deterministic_ids() -> None:
    """Give created rows ids derived from what they are (table + imported id; the
    n-th parsed statement), identically on both backends and for the Postgres
    mirror, so a case that creates rows can be compared: otherwise every new row
    carries a fresh uuid per store and tokenises differently."""
    import types
    import uuid

    from auto_ontology.catalog import sql_parse
    from auto_ontology.dal import model_interchange
    from auto_ontology.dal_rdf import model_interchange__all as port

    def wrap_resolve(module):
        original = module._resolve_entities_batch
        if getattr(original, "_det", False):
            return

        def resolve(table, items):
            return original(table, [(i, {**p, "id": _det_id(table.name, i)}) for i, p in items])

        resolve._det = True
        module._resolve_entities_batch = resolve

    wrap_resolve(model_interchange)
    wrap_resolve(_upstream())

    original_defaults = port.apply_defaults
    if not getattr(original_defaults, "_det", False):

        def apply_defaults(table, values):
            if values.get("imported_id") is not None and not values.get("id"):
                values = {**values, "id": _det_id(table, values["imported_id"])}
            return original_defaults(table, values)

        apply_defaults._det = True
        port.apply_defaults = apply_defaults

    if not isinstance(sql_parse.uuid, types.SimpleNamespace):
        def uuid4():
            _QUERY_COUNTER[0] += 1
            return uuid.UUID(_det_id("query", str(_QUERY_COUNTER[0])))

        sql_parse.uuid = types.SimpleNamespace(uuid4=uuid4)


def _apply(document, *, replace: bool) -> dict[str, Any]:
    """Apply through the DAL (the port under ``CATALOG_READ_BACKEND=rdf``).

    Under the RDF backend the same import is *also* run by upstream on the
    Postgres copy: reads that have no RDF port yet (zones, tag maps, a few term
    and SQL-attribute reads) fall back to Postgres, and would otherwise be
    graded against a copy the write never touched. Only the port's result is
    returned and graded; the mirror's ids differ (fresh uuids), so a case that
    creates rows shows those reads as tokens that cannot match.
    """
    import os

    from auto_ontology.dal import model_interchange

    _deterministic_ids()
    _QUERY_COUNTER[0] = 0
    result = model_interchange.apply_import_model(document, replace=replace)
    _QUERY_COUNTER[0] = 0
    if os.environ.get("CATALOG_READ_BACKEND", "postgres").lower() == "rdf":
        _upstream().apply_import_model(document, replace=replace)
    return result


def _import_yaml(document: dict[str, Any] | str, *, replace: bool) -> dict[str, Any]:
    from auto_ontology.server.model_interchange.schemas import AutoOntologyModelDocument

    payload = yaml.safe_load(document) if isinstance(document, str) else document
    summary = _apply(AutoOntologyModelDocument.model_validate(payload), replace=replace)
    summary["format"] = "auto_ontology"
    return summary


def roundtrip_replace() -> dict[str, Any]:
    """Export the fixture, import it back (replace, no embeddings), export again:
    the two exports must be identical."""
    first = _export_yaml()
    summary = _import_yaml(first, replace=True)
    second = _export_yaml()
    return {"summary": summary, "second_export_identical": _canon(first) == _canon(second)}


def roundtrip_merge() -> dict[str, Any]:
    first = _export_yaml()
    summary = _import_yaml(first, replace=False)
    return {"summary": summary, "second_export_identical": _canon(first) == _canon(_export_yaml())}


def merge_add_term(db_id: str, schema_id: str, table_id: str, film_id_col: str, title_col: str) -> dict[str, Any]:
    """Merge a small hand-written document: the existing film table (adopted by
    live id) plus one new Term representing it with one new SQL attribute."""
    document = {
        "data_layer": {
            "databases": [
                {
                    "id": db_id,
                    "dialect": "postgres",
                    "schemas": [
                        {
                            "id": schema_id,
                            "name": "public",
                            "database_name": "pagila",
                            "tables": [
                                {
                                    "id": table_id,
                                    "name": "film",
                                    "columns": [
                                        {"id": film_id_col, "name": "film_id"},
                                        {"id": title_col, "name": "title"},
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ]
        },
        "semantic_layer": {
            "terms": [
                {
                    "id": "import-term-1",
                    "name": "Screening",
                    "description": "A film shown to an audience.",
                    "represents": [table_id],
                }
            ],
            "sql_attributes": {
                "manual": [
                    {
                        "id": "import-sqlattr-1",
                        "name": "title length",
                        "description": "Characters in the title.",
                        "sql": "SELECT film_id, length(title) AS title_length FROM film",
                        "term_id": "import-term-1",
                    }
                ]
            },
        },
    }
    summary = _import_yaml(document, replace=False)
    again = _import_yaml(document, replace=False)  # idempotent: the second pass creates nothing
    return {"summary": summary, "second_pass": again}


def replace_drop_term(term_name: str) -> dict[str, Any]:
    """Re-import the fixture's export with ``replace`` after removing one Term
    (and what hangs off it): the Term, its attributes and SQL attributes go."""
    document = yaml.safe_load(_export_yaml())
    layer = document["semantic_layer"]
    dropped = [t for t in layer["terms"] if t["name"] == term_name]
    gone_terms = {t["id"] for t in dropped}
    gone_attrs = {a["id"] for t in dropped for a in t.get("columns_attributes", [])}
    layer["terms"] = [t for t in layer["terms"] if t["id"] not in gone_terms]
    layer["semantic_fks"] = [f for f in layer["semantic_fks"] if f["column_attribute_id"] not in gone_attrs]
    for key, items in layer["sql_attributes"].items():
        layer["sql_attributes"][key] = [a for a in items if a["term_id"] not in gone_terms]
    summary = _import_yaml(document, replace=True)
    return {"dropped": sorted(gone_terms), "summary": summary}


def empty_document(replace: bool) -> dict[str, Any]:
    from auto_ontology.server.model_interchange.schemas import AutoOntologyModelDocument

    return _apply(AutoOntologyModelDocument(), replace=replace)


def unresolvable_foreign_key() -> dict[str, Any]:
    """A malformed document must change nothing (upstream rolls back; the port
    checks before the first write) and raise the same error."""
    document = yaml.safe_load(_export_yaml())
    document["data_layer"]["foreign_keys"].append({"source_column_id": "ghost-a", "target_column_id": "ghost-b"})
    try:
        _import_yaml(document, replace=True)
    except Exception as exc:  # noqa: BLE001
        return {"raised": type(exc).__name__, "message": str(exc)}
    return {"raised": None}
