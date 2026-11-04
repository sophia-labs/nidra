# SPDX-License-Identifier: Apache-2.0
"""RDF ports of ``auto_ontology.dal.model_interchange``: the export reads and
the import write, on gardend.

Ported: ``apply_import_model`` (the import), ``fetch_export_rows`` and
``validate_database_ids`` (the export reads). ``assemble_export_document``,
``resolve_sql_column_ids`` and ``make_cached_sql_column_resolver`` stay
upstream's: the first is pure, the other two read through ``get_schemas``,
which is already a ported DAL read.

Import shape, level by level (database, schema, table, column, terms, column
attributes, SQL attributes, custom analyses): one read finds the rows a batch
names (``imported_id`` OR ``id``), then one write (``rdf.writes.commit``) sets
the batch (stamps for adopted rows, whole rows for new ones). A link table is
one write (re-setting an existing link changes nothing, like ``ON CONFLICT DO
NOTHING``).

Whole-import rollback is **compensated**, not transactional. Upstream's import is
one transaction; here each stage is its own write (under ``NIDRA_WRITES=commit``
one ledger frame, atomic by itself), because a later stage reads what an earlier
one wrote (the SQL stage parses against the catalog the import just wrote) and a
ledger transaction's frames are invisible to reads until it commits. So:

* every id reference that can be checked from the document alone (foreign keys,
  joins, a SQL attribute's term, a column attribute's column) is checked
  **before the first write** (``_preflight``): a malformed document writes nothing;
* ``writes.compensating()`` records, before each write, the prior state of every
  subject it touches; if a later stage fails (a statement that does not parse),
  one compensating write retracts every subject the import created and sets every
  subject it changed back to its prior values, then the error is raised. The
  graph is back where it was; the ledger keeps the import and its undo as lineage;
* a crash of the process between stages (no exception to catch) leaves the
  earlier stages written, as before.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Iterable

from auto_ontology.dal_rdf._common import P, client, iri, lit, select_rows, values, wrap
from auto_ontology.rdf.client import RDF_JSON
from auto_ontology.rdf.vocab import class_iri, prop_iri, ref_iri, row_id, row_iri_single
from auto_ontology.rdf import writes
from auto_ontology.rdf.writes import apply_defaults, data_graph_iri, delete_rows, delete_subjects
from auto_ontology.semantic.constants import (
    SEMANTIC_SOURCE,
    SQL_ATTR_SOURCE_BRIDGE,
    SQL_ATTR_SOURCE_MANUAL,
    SQL_ATTR_SOURCE_SQL,
    SQL_ATTR_SOURCE_TABLE,
)
from auto_ontology.utils.sample_values import dump_sample_values

if TYPE_CHECKING:  # pragma: no cover
    from auto_ontology.server.model_interchange.embed import ColumnCatalogMeta, ImportEmbedBuffer
    from auto_ontology.server.model_interchange.schemas import AutoOntologyModelDocument, ModelSqlAttribute

logger = logging.getLogger(__name__)

PORTS = ["apply_import_model", "fetch_export_rows", "validate_database_ids"]

_YAML_SOURCE_KEYS = ("manual", "table", "sql", "bridge_table")
_YAML_KEY_TO_SOURCE: dict[str, str] = {
    "manual": SQL_ATTR_SOURCE_MANUAL,
    "table": SQL_ATTR_SOURCE_TABLE,
    "sql": SQL_ATTR_SOURCE_SQL,
    "bridge_table": SQL_ATTR_SOURCE_BRIDGE,
}
_SOURCE_TO_YAML_KEY = {v: k for k, v in _YAML_KEY_TO_SOURCE.items()}

_CHUNK = 400


def _chunks(items: list, size: int = _CHUNK) -> Iterable[list]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _g() -> str:
    return iri(data_graph_iri())


def _run(ops: list[writes.Op]) -> None:
    """One stage's (or batch's) ops as ONE write (``rdf.writes.commit``)."""
    writes.commit(ops)


def _rows(table: str, ids: Iterable[str]) -> list[str]:
    return [row_iri_single(table, i) for i in ids]


def _json_whole(candidates: list[Any]) -> Any:
    """A JSONB/array column is stored twice (the whole value and, for a list, one
    triple per member). Pick the whole: the list that contains every other value."""
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    for c in candidates:
        if isinstance(c, list) and all(o in c for o in candidates if o is not c):
            return c
    return candidates[0]


# ---------------------------------------------------------------------------
# Export reads
# ---------------------------------------------------------------------------


def _scope(var: str, database_ids: list[str]) -> str:
    """An empty id list means **every** database (as upstream), not none."""
    if not database_ids:
        return ""
    return values(var, _rows("catalog_database", database_ids))


def _chain(t: str, sc: str, db: str) -> str:
    """Pattern: table ``?t`` in schema ``?sc`` in database ``?db`` (all must exist)."""
    return (
        f"?{t} a {iri(class_iri('catalog_table'))} ; {P.catalog_table.ref_schema_id} ?{sc} . "
        f"?{sc} a {iri(class_iri('catalog_schema'))} ; {P.catalog_schema.ref_database_id} ?{db} . "
        f"?{db} a {iri(class_iri('catalog_database'))} . "
    )


def validate_database_ids(database_ids: list[str]) -> None:
    """Raise ``UnknownDatabaseIdsError`` when any id is missing."""
    from auto_ontology.dal.model_interchange import UnknownDatabaseIdsError

    if not database_ids:
        return
    known = {row["id"] for row in select_rows("catalog_database", [])}
    unknown = [db_id for db_id in database_ids if db_id not in known]
    if unknown:
        raise UnknownDatabaseIdsError(unknown)


def _export_catalog(database_ids: list[str]) -> list[dict[str, Any]]:
    body = (
        f"{_scope('db', database_ids)} "
        f"?db a {iri(class_iri('catalog_database'))} ; {P.catalog_database.id} ?db_id ; {P.catalog_database.name} ?db_name . "
        f"?sc a {iri(class_iri('catalog_schema'))} ; {P.catalog_schema.ref_database_id} ?db ; "
        f"   {P.catalog_schema.id} ?schema_id ; {P.catalog_schema.name} ?schema_name . "
        f"?t a {iri(class_iri('catalog_table'))} ; {P.catalog_table.ref_schema_id} ?sc ; "
        f"   {P.catalog_table.id} ?table_id ; {P.catalog_table.name} ?table_name . "
        f"OPTIONAL {{ ?t {P.catalog_table.description} ?table_description }} "
        f"OPTIONAL {{ ?t {P.catalog_table.pk} ?pk FILTER(datatype(?pk) = <{RDF_JSON}>) }} "
        f"OPTIONAL {{ ?t {P.catalog_table.table_type} ?table_type }} "
        f"?c a {iri(class_iri('catalog_column'))} ; {P.catalog_column.ref_table_id} ?t ; "
        f"   {P.catalog_column.id} ?column_id ; {P.catalog_column.name} ?column_name . "
        f"OPTIONAL {{ ?c {P.catalog_column.description} ?column_description }} "
        f"OPTIONAL {{ ?c {P.catalog_column.data_type} ?column_type }} "
        f"OPTIONAL {{ ?c {P.catalog_column.sample_values} ?sample_values }} "
        f"OPTIONAL {{ ?c {P.catalog_column.is_unique} ?is_unique }} "
        f"OPTIONAL {{ ?c {P.catalog_column.is_nullable} ?is_nullable }} "
        f"OPTIONAL {{ ?c {P.catalog_column.ordinal_position} ?ordinal_position }}"
    )
    names = (
        "db_id db_name schema_id schema_name table_id table_name table_description pk table_type "
        "column_id column_name column_description column_type sample_values is_unique is_nullable ordinal_position"
    ).split()
    rows = client().select(f"SELECT {' '.join('?' + n for n in names)} WHERE {{ {wrap(body)} }}")
    out = []
    for r in rows:
        g = r.get
        out.append(
            {
                "db_id": g("db_id"),
                "db_name": g("db_name"),
                "schema_id": g("schema_id"),
                "schema_name": g("schema_name"),
                "table_id": g("table_id"),
                "table_name": g("table_name"),
                "table_description": g("table_description"),
                "pk": g("pk"),
                "table_type": g("table_type"),
                "column_id": g("column_id"),
                "column_live_id": g("column_id"),
                "column_name": g("column_name"),
                "column_description": g("column_description"),
                "column_type": g("column_type"),
                "sample_values": g("sample_values"),
                "is_unique": g("is_unique"),
                "is_nullable": g("is_nullable"),
                "ordinal_position": g("ordinal_position"),
            }
        )
    out.sort(
        key=lambda r: (
            r["db_name"], r["schema_name"], r["table_name"],
            r["ordinal_position"] is None, r["ordinal_position"] or 0, r["column_id"],
        )
    )
    return out


def _export_foreign_keys(database_ids: list[str]) -> list[dict[str, Any]]:
    body = (
        f"{_scope('db', database_ids)} "
        f"?fk a {iri(class_iri('column__foreign_key'))} ; {P.column__foreign_key.ref_source_column_id} ?src ; "
        f"   {P.column__foreign_key.ref_target_column_id} ?tgt . "
        f"?src a {iri(class_iri('catalog_column'))} ; {P.catalog_column.ref_table_id} ?t . "
        f"?tgt a {iri(class_iri('catalog_column'))} . "
        f"{_chain('t', 'sc', 'db')}"
    )
    rows = client().select(f"SELECT DISTINCT ?src ?tgt WHERE {{ {wrap(body)} }}")
    return sorted(
        ({"source_column_id": row_id(r["src"]), "target_column_id": row_id(r["tgt"])} for r in rows),
        key=lambda r: (r["source_column_id"], r["target_column_id"]),
    )


def _export_joins(database_ids: list[str]) -> list[dict[str, Any]]:
    """Joins whose **both** ends are in scope."""
    body = (
        f"{_scope('sdb', database_ids)} {_scope('tdb', database_ids)} "
        f"?j a {iri(class_iri('table__join'))} ; {P.table__join.ref_source_table_id} ?st ; {P.table__join.ref_target_table_id} ?tt . "
        f"OPTIONAL {{ ?j {P.table__join.join_columns} ?jc }} "
        f"{_chain('st', 'ssc', 'sdb')}{_chain('tt', 'tsc', 'tdb')}"
    )
    rows = client().select(f"SELECT ?st ?tt ?jc WHERE {{ {wrap(body)} }}")
    grouped: dict[tuple[str, str], list[Any]] = {}
    for r in rows:
        key = (row_id(r["st"]), row_id(r["tt"]))
        grouped.setdefault(key, [])
        if r.get("jc") is not None:
            grouped[key].append(r["jc"])
    return [
        {"source_table_id": s, "target_table_id": t, "join_columns": _json_whole(vals)}
        for (s, t), vals in sorted(grouped.items())
    ]


def _export_terms(database_ids: list[str]) -> list[dict[str, Any]]:
    """Terms represented by an in-scope table, with **all** their represents."""
    scoped = client().select(
        "SELECT DISTINCT ?term WHERE { "
        + wrap(
            f"{_scope('db', database_ids)} "
            f"?tl a {iri(class_iri('table__term'))} ; {P.table__term.ref_table_id} ?t ; {P.table__term.ref_term_id} ?term . "
            f"{_chain('t', 'sc', 'db')}"
        )
        + " }"
    )
    scoped_iris = [r["term"] for r in scoped]
    if not scoped_iris:
        return []
    terms = []
    for chunk in _chunks(scoped_iris):
        body = (
            f"{values('s', chunk)} ?s a {iri(class_iri('term'))} ; {P.term.id} ?id ; {P.term.name} ?name ; "
            f"{P.term.source} {lit(SEMANTIC_SOURCE)} . OPTIONAL {{ ?s {P.term.description} ?description }}"
        )
        terms.extend(client().select(f"SELECT ?id ?name ?description WHERE {{ {wrap(body)} }}"))
    terms.sort(key=lambda r: (r["name"], r["id"]))
    term_ids = [r["id"] for r in terms]
    if not term_ids:
        return []
    term_iris = _rows("term", term_ids)

    represents: dict[str, list[str]] = {}
    for chunk in _chunks(term_iris):
        rows = client().select(
            "SELECT ?term ?t WHERE { "
            + wrap(
                f"{values('term', chunk)} ?tt a {iri(class_iri('table__term'))} ; "
                f"{P.table__term.ref_term_id} ?term ; {P.table__term.ref_table_id} ?t ."
            )
            + " }"
        )
        for r in rows:
            represents.setdefault(row_id(r["term"]), []).append(row_id(r["t"]))
    for v in represents.values():
        v.sort()

    attributes: dict[str, list[dict[str, Any]]] = {}
    attr_rows: list[dict[str, Any]] = []
    for chunk in _chunks(term_iris):
        attr_rows.extend(
            client().select(
                "SELECT DISTINCT ?term ?a ?name ?description ?col WHERE { "
                + wrap(
                    f"{values('term', chunk)} ?l a {iri(class_iri('column_attribute__term'))} ; "
                    f"{P.column_attribute__term.ref_term_id} ?term ; {P.column_attribute__term.ref_attribute_id} ?a . "
                    f"?a a {iri(class_iri('column_attribute'))} ; {P.column_attribute.name} ?name . "
                    f"OPTIONAL {{ ?a {P.column_attribute.description} ?description }} "
                    f"?h a {iri(class_iri('column__has_attribute'))} ; {P.column__has_attribute.ref_attribute_id} ?a ; "
                    f"   {P.column__has_attribute.ref_column_id} ?col ."
                )
                + " }"
            )
        )
    for r in sorted(attr_rows, key=lambda r: (row_id(r["a"]), row_id(r["col"]))):
        attributes.setdefault(row_id(r["term"]), []).append(
            {
                "id": row_id(r["a"]),
                "name": r["name"],
                "description": r.get("description") or "",
                "column_id": row_id(r["col"]),
            }
        )

    return [
        {
            "id": row["id"],
            "name": row["name"],
            "description": row.get("description") or "",
            "represents": represents.get(row["id"], []),
            "columns_attributes": attributes.get(row["id"], []),
        }
        for row in terms
    ]


def _export_semantic_fks(database_ids: list[str]) -> list[dict[str, Any]]:
    body = (
        f"{_scope('db', database_ids)} "
        f"?f a {iri(class_iri('column__semantic_fk'))} ; {P.column__semantic_fk.ref_column_id} ?c ; "
        f"   {P.column__semantic_fk.ref_attribute_id} ?a ; {P.column__semantic_fk.use_in_join_paths} ?u . "
        f"?c a {iri(class_iri('catalog_column'))} ; {P.catalog_column.ref_table_id} ?t . "
        f"{_chain('t', 'sc', 'db')}"
    )
    rows = client().select(f"SELECT DISTINCT ?c ?a ?u WHERE {{ {wrap(body)} }}")
    return sorted(
        (
            {"column_id": row_id(r["c"]), "column_attribute_id": row_id(r["a"]), "use_in_join_paths": r["u"]}
            for r in rows
        ),
        key=lambda r: (r["column_id"], r["column_attribute_id"]),
    )


def _export_sql_owners(
    database_ids: list[str], link_table: str, owner_table: str, owner_column: str, extra_vars: str = "", extra: str = ""
) -> list[dict[str, Any]]:
    """The statement and database behind each SqlAttribute/CustomAnalysis."""
    body = (
        f"{_scope('db', database_ids)} "
        f"?q2t a {iri(class_iri('sql_query__table'))} ; {P.sql_query__table.ref_table_id} ?t ; {P.sql_query__table.ref_sql_query_id} ?q . "
        f"{_chain('t', 'sc', 'db')}"
        f"?db {P.catalog_database.name} ?database_name . "
        f"?q a {iri(class_iri('sql_query'))} ; {P.sql_query.sql_full_query} ?sql . "
        f"?l a {iri(class_iri(link_table))} ; {prop_ref(link_table, 'sql_query_id')} ?q ; {prop_ref(link_table, owner_column)} ?o . "
        f"?o a {iri(class_iri(owner_table))} ; {iri(prop_iri(owner_table, 'id'))} ?id ; {iri(prop_iri(owner_table, 'name'))} ?name . "
        f"OPTIONAL {{ ?o {iri(prop_iri(owner_table, 'description'))} ?description }} {extra}"
    )
    return client().select(
        f"SELECT DISTINCT ?id ?name ?description ?sql ?database_name {extra_vars} WHERE {{ {wrap(body)} }}"
    )


def prop_ref(table: str, column: str) -> str:
    return iri(ref_iri(table, column))


def _export_sql_attributes(database_ids: list[str]) -> list[dict[str, Any]]:
    rows = _export_sql_owners(
        database_ids,
        "sql_attribute__sql",
        "sql_attribute",
        "attribute_id",
        extra_vars="?expression ?source ?term",
        extra=(
            f"OPTIONAL {{ ?o {P.sql_attribute.expression} ?expression }} "
            f"OPTIONAL {{ ?o {P.sql_attribute.source} ?source }} "
            f"?st2 a {iri(class_iri('sql_attribute__term'))} ; {P.sql_attribute__term.ref_attribute_id} ?o ; "
            f"    {P.sql_attribute__term.ref_term_id} ?term . "
            f"?term a {iri(class_iri('term'))} ."
        ),
    )
    out = [
        {
            "id": r["id"],
            "name": r["name"],
            "description": r.get("description") or "",
            "sql": r["sql"],
            "database_name": r["database_name"],
            "expression": r.get("expression") or "",
            "source": r.get("source") or SQL_ATTR_SOURCE_MANUAL,
            "term_id": row_id(r["term"]),
        }
        for r in rows
    ]
    out.sort(key=lambda r: (r["name"], r["id"], r["database_name"]))
    return out


def _export_custom_analyses(database_ids: list[str]) -> list[dict[str, Any]]:
    rows = _export_sql_owners(database_ids, "custom_analysis__sql", "custom_analysis", "analysis_id")
    out = [
        {
            "id": r["id"],
            "name": r["name"],
            "description": r.get("description") or "",
            "sql": r["sql"],
            "database_name": r["database_name"],
        }
        for r in rows
    ]
    out.sort(key=lambda r: (r["name"], r["id"], r["database_name"]))
    return out


def fetch_export_rows(database_ids: list[str]) -> dict[str, Any]:
    """The raw rows ``assemble_export_document`` turns into a document."""
    return {
        "catalog": _export_catalog(database_ids),
        "foreign_keys": _export_foreign_keys(database_ids),
        "joins": _export_joins(database_ids),
        "terms": _export_terms(database_ids),
        "semantic_fks": _export_semantic_fks(database_ids),
        "sql_attributes": _export_sql_attributes(database_ids),
        "custom_analyses": _export_custom_analyses(database_ids),
    }


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


def _err(yaml_id: str, kind: str):
    from auto_ontology.dal.model_interchange import ModelImportValidationError

    return ModelImportValidationError(
        f"Cannot resolve {kind} id {yaml_id!r} — missing from catalog/semantic import",
    )


def _remap(id_map: dict[str, str], yaml_id: str, *, kind: str) -> str:
    live_id = id_map.get(yaml_id)
    if not live_id:
        raise _err(yaml_id, kind)
    return live_id


def _remap_optional(id_map: dict[str, str], yaml_id: str) -> str | None:
    return id_map.get(yaml_id)


def _lookup_existing(table: str, ids: list[str]) -> list[dict[str, Any]]:
    """Rows whose ``imported_id`` OR ``id`` is one of ``ids``, sorted by id."""
    found: dict[str, dict[str, Any]] = {}
    for chunk in _chunks(list(dict.fromkeys(ids))):
        lits = ", ".join(lit(x) for x in chunk)
        rows = select_rows(table, ["imported_id"], extra=f"?s {P_id(table)} ?id . FILTER(?id IN ({lits}) || ?imported_id IN ({lits}))")
        for r in rows:
            found[r["id"]] = {"id": r["id"], "imported_id": r.get("imported_id")}
    return sorted(found.values(), key=lambda r: r["id"])


def P_id(table: str) -> str:
    return iri(prop_iri(table, "id"))


def _found_yaml_ids(table: str, ids: list[str]) -> set[str]:
    wanted = set(ids)
    out: set[str] = set()
    for row in _lookup_existing(table, ids):
        for key in (row["imported_id"], row["id"]):
            if key in wanted:
                out.add(key)
    return out


def _plan_batch(
    table: str, items: list[tuple[str, dict[str, Any]]]
) -> tuple[dict[str, tuple[str, bool]], list[writes.Op]]:
    """``({yaml_id: (live_id, created)}, ops to write)`` for a same-table batch.

    Matches on ``imported_id`` **or** the live ``id``; a row adopted by ``id`` has
    its ``imported_id`` stamped if it had none; the rest are created (defaults
    applied, so the uuid ids are decided here). Nothing is written: the caller
    writes the ops, alone or merged with the rest of its stage.
    """
    if not items:
        return {}, []
    deduped: dict[str, dict[str, Any]] = {}
    for imported_id, create_props in items:
        deduped.setdefault(imported_id, create_props)
    imported_ids = list(deduped)

    existing: dict[str, str] = {}
    rows_by_id: dict[str, dict[str, Any]] = {}
    for row in _lookup_existing(table, imported_ids):
        rows_by_id[row["id"]] = row
        if row["imported_id"] in deduped:
            existing[row["imported_id"]] = row["id"]
        elif row["id"] in deduped:
            existing.setdefault(row["id"], row["id"])

    result: dict[str, tuple[str, bool]] = {}
    lines: list[writes.Op] = []
    stamped: set[str] = set()
    to_create: list[tuple[str, dict[str, Any]]] = []
    for imported_id, create_props in deduped.items():
        live_id = existing.get(imported_id)
        if live_id is None:
            to_create.append((imported_id, dict(create_props or {})))
            continue
        result[imported_id] = (live_id, False)
        # Stamp only when unset (overwriting would relabel a row another payload owns).
        if rows_by_id[live_id]["imported_id"] is None and live_id not in stamped:
            stamped.add(live_id)
            lines.extend(writes.column_ops(table, row_iri_single(table, live_id), "imported_id", imported_id, clear=False))
    for imported_id, props in to_create:
        stored = apply_defaults(table, {**props, "imported_id": imported_id})
        lines.extend(writes.row_ops(table, stored))
        result[imported_id] = (stored["id"], True)
    return result, lines


def _resolve_entities_batch(table: str, items: list[tuple[str, dict[str, Any]]]) -> dict[str, tuple[str, bool]]:
    result, ops = _plan_batch(table, items)
    _run(ops)
    return result


def _link(table: str, rows: list[dict[str, Any]], *, one_per: str | None = None) -> None:
    """Idempotent link-table insert (``ON CONFLICT DO NOTHING``); ``[]`` is a no-op.

    ``one_per`` names a column with its own UNIQUE constraint (an attribute has
    one Term): a row whose value is already linked is skipped, the first wins.
    """
    if not rows:
        return
    if one_per:
        taken: set[str] = set()
        for chunk in _chunks(list(dict.fromkeys(r[one_per] for r in rows))):
            found = client().select(
                "SELECT ?v WHERE { "
                + wrap(f"{values('v', _rows(_one_per_target(table, one_per), chunk))} ?l {prop_ref(table, one_per)} ?v .")
                + " }"
            )
            taken.update(row_id(r["v"]) for r in found)
        kept = []
        for r in rows:
            if r[one_per] in taken:
                continue
            taken.add(r[one_per])
            kept.append(r)
        rows = kept
    ops: list[writes.Op] = []
    for r in rows:
        ops.extend(writes.row_ops(table, r))
    _run(ops)


def _one_per_target(table: str, column: str) -> str:
    from auto_ontology.dal.schema import METADATA

    fk = next(fk for fk in METADATA.tables[table].foreign_keys if fk.parent.name == column)
    return fk.column.table.name


def _database_names_for_terms(term_ids: list[str]) -> dict[str, str]:
    if not term_ids:
        return {}
    names: dict[str, str] = {}
    rows: list[dict[str, Any]] = []
    for chunk in _chunks(_rows("term", term_ids)):
        rows.extend(
            client().select(
                "SELECT ?term ?dbid ?dbname WHERE { "
                + wrap(
                    f"{values('term', chunk)} ?tl a {iri(class_iri('table__term'))} ; "
                    f"{P.table__term.ref_term_id} ?term ; {P.table__term.ref_table_id} ?t . "
                    f"{_chain('t', 'sc', 'db')} ?db {P.catalog_database.id} ?dbid ; {P.catalog_database.name} ?dbname ."
                )
                + " }"
            )
        )
    for r in sorted(rows, key=lambda r: r["dbid"]):
        names.setdefault(row_id(r["term"]), r["dbname"])
    return names


def _database_names_for_columns(column_ids: list[str]) -> dict[str, str]:
    if not column_ids:
        return {}
    names: dict[str, str] = {}
    rows: list[dict[str, Any]] = []
    for chunk in _chunks(_rows("catalog_column", column_ids)):
        rows.extend(
            client().select(
                "SELECT ?c ?dbid ?dbname WHERE { "
                + wrap(
                    f"{values('c', chunk)} ?c a {iri(class_iri('catalog_column'))} ; {P.catalog_column.ref_table_id} ?t . "
                    f"{_chain('t', 'sc', 'db')} ?db {P.catalog_database.id} ?dbid ; {P.catalog_database.name} ?dbname ."
                )
                + " }"
            )
        )
    for r in sorted(rows, key=lambda r: r["dbid"]):
        names.setdefault(row_id(r["c"]), r["dbname"])
    return names


def _all_payload_ids(document: AutoOntologyModelDocument) -> dict[str, set[str]]:
    sem = document.semantic_layer
    return {
        "catalog": {
            x
            for db in document.data_layer.databases
            for x in [db.id]
            + [sc.id for sc in db.schemas]
            + [t.id for sc in db.schemas for t in sc.tables]
            + [c.id for sc in db.schemas for t in sc.tables for c in t.columns]
        },
        "terms": {t.id for t in sem.terms},
        "attrs": {a.id for t in sem.terms for a in t.columns_attributes},
    }


def _preflight(document: AutoOntologyModelDocument) -> None:
    """Raise, before the first write, what upstream would raise mid-transaction.

    There is no rollback on this backend, so every reference that can be checked
    from the document alone is checked here, in upstream's order.
    """
    ids = _all_payload_ids(document)
    known = set(ids["catalog"])
    for fk in document.data_layer.foreign_keys:
        if fk.source_column_id not in known:
            raise _err(fk.source_column_id, "foreign-key source column")
        if fk.target_column_id not in known:
            raise _err(fk.target_column_id, "foreign-key target column")
    for join in document.data_layer.joins:
        if join.source_table_id not in known:
            raise _err(join.source_table_id, "join source table")
        if join.target_table_id not in known:
            raise _err(join.target_table_id, "join target table")
    known |= ids["terms"]
    for term in document.semantic_layer.terms:
        for attr in term.columns_attributes:
            if attr.column_id not in known:
                raise _err(attr.column_id, "column attribute column")
    known |= ids["attrs"]
    grouped = [
        a
        for key in _YAML_SOURCE_KEYS
        for a in getattr(document.semantic_layer.sql_attributes, key)
    ]
    if grouped:
        found = _found_yaml_ids("sql_attribute", [a.id for a in grouped])
        known |= {a.id for a in grouped}
        for attr in grouped:
            if attr.id not in found and attr.term_id not in known:
                raise _err(attr.term_id, "sql attribute term")


def _scoped_table_iris(live_db_ids: list[str]) -> set[str]:
    out: set[str] = set()
    for chunk in _chunks(_rows("catalog_database", live_db_ids)):
        rows = client().select(
            "SELECT DISTINCT ?t WHERE { " + wrap(f"{values('db', chunk)} {_chain('t', 'sc', 'db')}") + " }"
        )
        out.update(r["t"] for r in rows)
    return out


def _identity(row: dict[str, Any]) -> str:
    """``coalesce(imported_id, id)``: what an import payload names."""
    return row["imported_id"] if row.get("imported_id") is not None else row["id"]


def _delete_scoped_semantics_not_in_payload(document: AutoOntologyModelDocument, live_db_ids: list[str]) -> None:
    """Drop in-scope semantics the payload does not mention (one batch per kind)."""
    if not live_db_ids:
        return
    keep_terms = {term.id for term in document.semantic_layer.terms}
    keep_attrs = {a.id for term in document.semantic_layer.terms for a in term.columns_attributes}
    keep_sql_attrs = {
        a.id for key in _YAML_SOURCE_KEYS for a in getattr(document.semantic_layer.sql_attributes, key)
    }
    keep_analyses = {ca.id for ca in document.semantic_layer.custom_analyses}
    scoped_tables = _scoped_table_iris(live_db_ids)

    for owner_table, link_table, owner_column, keep in (
        ("sql_attribute", "sql_attribute__sql", "attribute_id", keep_sql_attrs),
        ("custom_analysis", "custom_analysis__sql", "analysis_id", keep_analyses),
    ):
        candidates: dict[str, dict[str, Any]] = {}
        for chunk in _chunks(_rows("catalog_database", live_db_ids)):
            rows = client().select(
                "SELECT DISTINCT ?o ?imported_id WHERE { "
                + wrap(
                    f"{values('db', chunk)} {_chain('t', 'sc', 'db')} "
                    f"?q2t a {iri(class_iri('sql_query__table'))} ; {P.sql_query__table.ref_table_id} ?t ; "
                    f"    {P.sql_query__table.ref_sql_query_id} ?q . "
                    f"?l a {iri(class_iri(link_table))} ; {prop_ref(link_table, 'sql_query_id')} ?q ; "
                    f"   {prop_ref(link_table, owner_column)} ?o . "
                    f"OPTIONAL {{ ?o {iri(prop_iri(owner_table, 'imported_id'))} ?imported_id }}"
                )
                + " }"
            )
            for r in rows:
                candidates[row_id(r["o"])] = {"id": row_id(r["o"]), "imported_id": r.get("imported_id")}
        doomed = [r["id"] for r in candidates.values() if _identity(r) not in keep]
        if doomed:
            delete_rows(owner_table, sorted(doomed))

    # Column attributes whose (plain string) table_id is a scoped table.
    doomed = []
    for chunk in _chunks(_rows("catalog_database", live_db_ids)):
        rows = client().select(
            "SELECT DISTINCT ?a ?imported_id WHERE { "
            + wrap(
                f"{values('db', chunk)} {_chain('t', 'sc', 'db')} ?t {P.catalog_table.id} ?tid . "
                f"?a a {iri(class_iri('column_attribute'))} ; {P.column_attribute.table_id} ?tid . "
                f"OPTIONAL {{ ?a {P.column_attribute.imported_id} ?imported_id }}"
            )
            + " }"
        )
        doomed.extend(row_id(r["a"]) for r in rows if _identity({"id": row_id(r["a"]), "imported_id": r.get("imported_id")}) not in keep_attrs)
    if doomed:
        delete_rows("column_attribute", sorted(set(doomed)))

    # Only terms whose every representing table is inside this import.
    links = client().select(
        "SELECT ?term ?t WHERE { "
        + wrap(f"?tl a {iri(class_iri('table__term'))} ; {P.table__term.ref_term_id} ?term ; {P.table__term.ref_table_id} ?t .")
        + " }"
    )
    inside = {r["term"] for r in links if r["t"] in scoped_tables}
    outside = {r["term"] for r in links if r["t"] not in scoped_tables}
    terms = select_rows("term", ["imported_id", "source"])
    doomed = [
        r["id"]
        for r in terms
        if r.get("source") == SEMANTIC_SOURCE
        and row_iri_single("term", r["id"]) in inside
        and row_iri_single("term", r["id"]) not in outside
        and _identity(r) not in keep_terms
    ]
    if doomed:
        delete_rows("term", sorted(doomed))


def _import_catalog(
    document: AutoOntologyModelDocument,
    id_map: dict[str, str],
    created: dict[str, int],
    skipped: dict[str, int],
    embed_buffer: ImportEmbedBuffer | None,
    column_meta: dict[str, ColumnCatalogMeta],
) -> list[str]:
    from auto_ontology.server.model_interchange.embed import (
        ColumnCatalogMeta,
        build_column_data_row,
        build_table_data_row,
    )

    databases = document.data_layer.databases
    db_names = {
        db.id: (db.schemas[0].database_name or db.schemas[0].name or "" if db.schemas else "")
        for db in databases
    }
    db_results = _resolve_entities_batch(
        "catalog_database", [(db.id, {"name": db_names[db.id] or db.id}) for db in databases]
    )
    live_db_ids: list[str] = []
    for db in databases:
        live_db_id, was_created = db_results[db.id]
        id_map[db.id] = live_db_id
        live_db_ids.append(live_db_id)
        (created if was_created else skipped)["databases"] += 1

    schema_db_names: dict[str, str] = {}
    schema_items: list[tuple[str, dict[str, Any]]] = []
    for db in databases:
        for schema in db.schemas:
            schema_db_names[schema.id] = schema.database_name or db_names[db.id]
            schema_items.append((schema.id, {"name": schema.name, "database_id": id_map[db.id]}))
    schema_results = _resolve_entities_batch("catalog_schema", schema_items)
    for schema_id, (live_id, was_created) in schema_results.items():
        id_map[schema_id] = live_id
        (created if was_created else skipped)["schemas"] += 1

    table_items: list[tuple[str, dict[str, Any]]] = []
    for db in databases:
        for schema in db.schemas:
            for table in schema.tables:
                table_items.append(
                    (
                        table.id,
                        {
                            "name": table.name,
                            "description": table.description,
                            "pk": table.pk,
                            "table_type": table.type,
                            "schema_id": id_map[schema.id],
                        },
                    )
                )
    table_results = _resolve_entities_batch("catalog_table", table_items)
    for table_id, (live_id, was_created) in table_results.items():
        id_map[table_id] = live_id
        (created if was_created else skipped)["tables"] += 1

    column_items: list[tuple[str, dict[str, Any]]] = []
    for db in databases:
        for schema in db.schemas:
            schema_db_name = schema_db_names.get(schema.id, "")
            for table in schema.tables:
                for ordinal, column in enumerate(table.columns, start=1):
                    column_meta[column.id] = ColumnCatalogMeta(
                        name=column.name,
                        description=column.description,
                        data_type=column.type,
                        sample_values=column.sample_values,
                        is_unique=column.is_unique,
                        table_yaml_id=table.id,
                        table_name=table.name,
                        schema_name=schema.name,
                        database_name=schema_db_name,
                    )
                    column_items.append(
                        (
                            column.id,
                            {
                                "name": column.name,
                                "description": column.description,
                                "data_type": column.type,
                                "sample_values": dump_sample_values(column.sample_values),
                                "is_unique": column.is_unique,
                                "is_nullable": column.is_nullable,
                                "ordinal_position": ordinal,
                                "table_id": id_map[table.id],
                            },
                        )
                    )
    column_results = _resolve_entities_batch("catalog_column", column_items)
    for column_id, (live_id, was_created) in column_results.items():
        id_map[column_id] = live_id
        (created if was_created else skipped)["columns"] += 1

    if embed_buffer is not None:
        for db in databases:
            for schema in db.schemas:
                schema_db_name = schema_db_names.get(schema.id, "")
                for table in schema.tables:
                    live_table_id, tbl_created = table_results[table.id]
                    specs: list[dict[str, Any]] = []
                    for column in table.columns:
                        live_col_id, col_created = column_results[column.id]
                        if col_created:
                            embed_buffer.data_rows.append(
                                build_column_data_row(
                                    live_id=live_col_id,
                                    column_name=column.name,
                                    column_description=column.description,
                                    data_type=column.type,
                                    sample_values=column.sample_values,
                                    table_name=table.name,
                                    schema_name=schema.name,
                                    database_name=schema_db_name,
                                ),
                            )
                        specs.append(
                            {"column_name": column.name, "data_type": column.type, "description": column.description}
                        )
                    if tbl_created:
                        embed_buffer.data_rows.append(
                            build_table_data_row(
                                live_id=live_table_id,
                                table_name=table.name,
                                table_description=table.description,
                                schema_name=schema.name,
                                database_name=schema_db_name,
                                columns=specs,
                            ),
                        )
    return live_db_ids


def _import_foreign_keys(document: AutoOntologyModelDocument, id_map: dict[str, str]) -> None:
    _link(
        "column__foreign_key",
        [
            {
                "source_column_id": _remap(id_map, fk.source_column_id, kind="foreign-key source column"),
                "target_column_id": _remap(id_map, fk.target_column_id, kind="foreign-key target column"),
            }
            for fk in document.data_layer.foreign_keys
        ],
    )


def _import_joins(document: AutoOntologyModelDocument, id_map: dict[str, str]) -> None:
    rows = [
        {
            "source_table_id": _remap(id_map, j.source_table_id, kind="join source table"),
            "target_table_id": _remap(id_map, j.target_table_id, kind="join target table"),
            "join_columns": j.join_columns,
        }
        for j in document.data_layer.joins
    ]
    if not rows:
        return
    # ON CONFLICT DO UPDATE SET join_columns: the last row of a duplicate pair wins.
    latest = {(r["source_table_id"], r["target_table_id"]): r for r in rows}
    ops: list[writes.Op] = []
    for (s, t), r in latest.items():
        # every column set (a NULL clears): the pair upserts, join_columns replaced whole
        ops.extend(writes.update_ops("table__join", row_iri_for("table__join", {"source_table_id": s, "target_table_id": t}), r))
    _run(ops)


def row_iri_for(table: str, pk: dict[str, str]) -> str:
    from auto_ontology.rdf.vocab import row_iri

    return row_iri(table, pk)


def _import_terms(
    document: AutoOntologyModelDocument,
    id_map: dict[str, str],
    created: dict[str, int],
    skipped: dict[str, int],
    embed_buffer: ImportEmbedBuffer | None,
) -> None:
    from auto_ontology.server.model_interchange.embed import build_term_semantic_rows

    terms = document.semantic_layer.terms
    term_results = _resolve_entities_batch(
        "term",
        [(t.id, {"name": t.name, "description": t.description, "source": SEMANTIC_SOURCE}) for t in terms],
    )
    represents_rows: list[dict[str, Any]] = []
    newly_created: list[str] = []
    live_terms: list[str] = []
    for term in terms:
        live_term_id, was_created = term_results[term.id]
        id_map[term.id] = live_term_id
        live_terms.append(live_term_id)
        if was_created:
            created["terms"] += 1
            newly_created.append(live_term_id)
        else:
            skipped["terms"] += 1
        for table_id in term.represents:
            live_table_id = _remap_optional(id_map, table_id)
            if live_table_id is None:
                logger.debug("import: term %s represents out-of-scope table %s; skipping", term.id, table_id)
                continue
            represents_rows.append({"term_id": live_term_id, "table_id": live_table_id})

    # The payload is authoritative about which tables represent a term, but only
    # for the tables it carries (every live id this document resolved).
    resolved = sorted(set(id_map.values()))
    stale: list[str] = []
    for term_chunk in _chunks(sorted(set(live_terms))):
        for table_chunk in _chunks(resolved):
            rows = client().select(
                "SELECT ?tl WHERE { "
                + wrap(
                    f"{values('term', _rows('term', term_chunk))} {values('t', _rows('catalog_table', table_chunk))} "
                    f"?tl a {iri(class_iri('table__term'))} ; {P.table__term.ref_term_id} ?term ; {P.table__term.ref_table_id} ?t ."
                )
                + " }"
            )
            stale.extend(r["tl"] for r in rows)
    if stale:
        delete_subjects("table__term", stale)
    _link("table__term", represents_rows)

    if embed_buffer is not None and newly_created:
        db_names = _database_names_for_terms(newly_created)
        for term in terms:
            live_term_id, was_created = term_results[term.id]
            if not was_created:
                continue
            embed_buffer.semantic_rows.extend(
                build_term_semantic_rows(
                    database_name=db_names.get(live_term_id, ""),
                    live_id=live_term_id,
                    name=term.name,
                    description=term.description,
                ),
            )


def _import_column_attributes(
    document: AutoOntologyModelDocument,
    id_map: dict[str, str],
    created: dict[str, int],
    skipped: dict[str, int],
    embed_buffer: ImportEmbedBuffer | None,
    column_meta: dict[str, ColumnCatalogMeta],
) -> None:
    from auto_ontology.server.model_interchange.embed import build_column_attribute_semantic_rows

    attr_items: list[tuple[str, dict[str, Any]]] = []
    context: dict[str, dict[str, Any]] = {}
    for term in document.semantic_layer.terms:
        live_term_id = _remap(id_map, term.id, kind="term")
        for attr in term.columns_attributes:
            col_ctx = column_meta.get(attr.column_id)
            live_table_id = (
                _remap(id_map, col_ctx.table_yaml_id, kind="column attribute table") if col_ctx else ""
            )
            attr_items.append(
                (
                    attr.id,
                    {
                        "name": attr.name,
                        "description": attr.description,
                        "source": SEMANTIC_SOURCE,
                        "term_name": term.name,
                        "source_column": col_ctx.name if col_ctx else "",
                        "table_id": live_table_id,
                    },
                )
            )
            context[attr.id] = {
                "attr": attr,
                "term": term,
                "col_ctx": col_ctx,
                "live_term_id": live_term_id,
                "live_table_id": live_table_id,
            }
    attr_results = _resolve_entities_batch("column_attribute", attr_items)

    has_attribute_rows: list[dict[str, Any]] = []
    property_of_rows: list[dict[str, Any]] = []
    for attr_id, (live_attr_id, was_created) in attr_results.items():
        ctx = context[attr_id]
        attr, term, col_ctx = ctx["attr"], ctx["term"], ctx["col_ctx"]
        id_map[attr_id] = live_attr_id
        (created if was_created else skipped)["column_attributes"] += 1
        has_attribute_rows.append(
            {"column_id": _remap(id_map, attr.column_id, kind="column attribute column"), "attribute_id": live_attr_id}
        )
        property_of_rows.append({"attribute_id": live_attr_id, "term_id": ctx["live_term_id"]})
        if was_created and embed_buffer is not None and col_ctx is not None:
            embed_buffer.semantic_rows.extend(
                build_column_attribute_semantic_rows(
                    database_name=col_ctx.database_name,
                    live_id=live_attr_id,
                    name=attr.name,
                    description=attr.description,
                    term_name=term.name,
                    source_column=col_ctx.name,
                    table_id=ctx["live_table_id"],
                    table_name=col_ctx.table_name,
                    is_unique=col_ctx.is_unique,
                    sample_values=col_ctx.sample_values,
                    schema_name=col_ctx.schema_name,
                ),
            )
    _link("column__has_attribute", has_attribute_rows)
    _link("column_attribute__term", property_of_rows, one_per="attribute_id")


def _import_semantic_fks(document: AutoOntologyModelDocument, id_map: dict[str, str]) -> None:
    rows = []
    for fk in document.semantic_layer.semantic_fks:
        column_id = _remap_optional(id_map, fk.column_id)
        attribute_id = _remap_optional(id_map, fk.column_attribute_id)
        if column_id is None or attribute_id is None:
            logger.debug("import: semantic fk %s -> %s is out of scope; skipping", fk.column_id, fk.column_attribute_id)
            continue
        rows.append({"column_id": column_id, "attribute_id": attribute_id, "use_in_join_paths": fk.use_in_join_paths})
    if not rows:
        return
    # ON CONFLICT DO UPDATE SET use_in_join_paths: replace the flag, insert the rest.
    latest = {(r["column_id"], r["attribute_id"]): r for r in rows}
    ops: list[writes.Op] = []
    for (c, a), r in latest.items():
        ops.extend(writes.update_ops("column__semantic_fk", row_iri_for("column__semantic_fk", {"column_id": c, "attribute_id": a}), r))
    _run(ops)


class _SqlBatch:
    """The rows ``add_query`` would write for a stage's statements, accumulated
    and written in one request. A statement is matched by its exact text."""

    def __init__(self, texts: Iterable[str]) -> None:
        from auto_ontology.dal.schema import METADATA

        self._metadata = METADATA
        texts = list(dict.fromkeys(texts))
        self.sql_ids: dict[str, str] = {}
        for chunk in _chunks(texts):
            rows = client().select(
                "SELECT ?s ?t WHERE { "
                + wrap(
                    f"{values('t', chunk, as_iri=False)} ?s a {iri(class_iri('sql_query'))} ; {P.sql_query.sql_full_query} ?t ."
                )
                + " }"
            )
            for r in sorted(rows, key=lambda r: r["s"], reverse=True):
                self.sql_ids[r["t"]] = row_id(r["s"])
        self.lines: list[writes.Op] = []
        self._refs: dict[tuple[str, str, str], list[list[str]]] = {}

    def _node_id(self, node) -> str:
        from auto_ontology.catalog.constants import Labels
        from auto_ontology.catalog.store import registry

        match = node.get_match_props()
        if node.get_label() == Labels.SQL:
            text = match["sql_full_query"]
            if text not in self.sql_ids:
                spec_props = registry.projected(Labels.SQL, node.get_properties())
                stored = apply_defaults("sql_query", spec_props)
                self.lines.extend(writes.row_ops("sql_query", stored))
                self.sql_ids[text] = stored["id"]
            return self.sql_ids[text]
        # Table / Column / SqlAttribute / CustomAnalysis nodes here always exist and
        # carry their live id; an existing row with no override is left untouched.
        return match["id"]

    def add(self, edges) -> None:
        from auto_ontology.catalog.store import registry
        from auto_ontology.catalog.store.queries import _link_kind, _reject_nested

        for source, target, properties in edges:
            for endpoint in (source, target):
                _reject_nested(endpoint.get_properties())
                if endpoint.get_override_existing_props():
                    _reject_nested(endpoint.get_override_existing_props())
            _reject_nested(properties)
            spec = registry.link_spec(_link_kind(properties), source.get_label(), target.get_label())
            if spec.is_parent_link:
                raise NotImplementedError("CONTAINS edges are not part of a model import")
            source_id = self._node_id(source)
            target_id = self._node_id(target)
            values_ = {spec.source_column: source_id, spec.target_column: target_id}
            payload = {k: v for k, v in properties.items() if k in spec.property_columns}
            if not payload:
                self.lines.extend(writes.row_ops(spec.table.name, values_))
            elif "refs" in payload and spec.table.name in ("column__join", "column__union"):
                self._refs.setdefault((spec.table.name, source_id, target_id), []).append(list(payload["refs"]))
            else:
                self.lines.extend(writes.row_ops(spec.table.name, {**values_, **payload}))

    def operations(self, extra_lines: list[writes.Op]) -> list[writes.Op]:
        """The stage's ops: refs accumulate (append, de-duplicate) on conflict."""
        lines = list(extra_lines) + self.lines
        for (table, src, tgt), incoming in self._refs.items():
            subject = row_iri_for(table, {"source_column_id": src, "target_column_id": tgt})
            found = client().select(
                "SELECT ?refs WHERE { "
                + wrap(f"<{subject}> {iri(prop_iri(table, 'refs'))} ?refs FILTER(datatype(?refs) = <{RDF_JSON}>)")
                + " }"
            )
            existing = found[0]["refs"] if found else None
            state = list(existing) if existing is not None else None
            for refs in incoming:
                state = list(refs) if state is None else sorted(set(state + refs))
            if existing is not None:
                # the list column rewritten whole: one `set values[]` (rdf:JSON + members)
                lines.extend(writes.update_ops(table, subject, {"refs": state}))
            else:
                lines.extend(writes.row_ops(table, {"source_column_id": src, "target_column_id": tgt, "refs": state}))
        return lines


def _cached_dialects_and_schemas(cache: dict, database_name: str | None):
    from auto_ontology.server.sql_utils import get_dialects, get_schemas

    if database_name not in cache:
        cache[database_name] = (get_dialects(database_name), get_schemas(database_name))
    return cache[database_name]


def _persist_sql_object(
    batch: _SqlBatch,
    *,
    node_label: str,
    node_id: str,
    name: str,
    description: str,
    sql: str,
    database_name: str | None,
    schema_cache: dict,
    extra_props: dict[str, Any] | None = None,
) -> None:
    """Parse the SQL and queue the owner's links the way ``add_query`` writes them."""
    from auto_ontology.catalog.constants import Props
    from auto_ontology.catalog.model import CatalogNode
    from auto_ontology.server.sql_utils import validate_sql

    dialects, schemas = _cached_dialects_and_schemas(schema_cache, database_name)
    query_obj = validate_sql(sql, dialects, schemas)
    props: dict[str, Any] = {"name": name, "description": description}
    if extra_props:
        props.update(extra_props)
    node = CatalogNode(name=name, label=node_label, props=props, existing_id=node_id, match_props={"id": node_id})
    query_obj.sql_node.match_props = {"sql_full_query": sql}
    query_obj.edges.append((node, query_obj.sql_node, {Props.ANALYSIS_ID: node_id}))
    batch.add(query_obj.get_edges())


def _import_sql_attributes(
    document: AutoOntologyModelDocument,
    id_map: dict[str, str],
    created: dict[str, int],
    skipped: dict[str, int],
    embed_buffer: ImportEmbedBuffer | None,
    term_names: dict[str, str],
    schema_cache: dict,
) -> None:
    from auto_ontology.server.model_interchange.embed import build_sql_attribute_semantic_row

    grouped = [
        (_YAML_KEY_TO_SOURCE[key], attr)
        for key in _YAML_SOURCE_KEYS
        for attr in getattr(document.semantic_layer.sql_attributes, key)
    ]
    attr_results, plan_lines = _plan_batch(
        "sql_attribute",
        [
            (attr.id, {"name": attr.name, "description": attr.description, "expression": attr.sql, "source": source})
            for source, attr in grouped
        ],
    )
    for _source, attr in grouped:
        id_map[attr.id] = attr_results[attr.id][0]

    newly_created_terms = list(
        {
            _remap(id_map, attr.term_id, kind="sql attribute term")
            for _source, attr in grouped
            if attr_results[attr.id][1]
        }
    )
    db_names = _database_names_for_terms(newly_created_terms)
    batch = _SqlBatch(attr.sql for _s, attr in grouped if attr_results[attr.id][1])
    link_lines: list[writes.Op] = []
    for source, attr in grouped:
        live_attr_id, was_created = attr_results[attr.id]
        if not was_created:
            skipped["sql_attributes"] += 1
            continue
        created["sql_attributes"] += 1
        live_term_id = _remap(id_map, attr.term_id, kind="sql attribute term")
        database_name = db_names.get(live_term_id)
        # (detach_existing_sql_edges / link_to_term replace edges of a *new* id: nothing to detach.)
        _persist_sql_object(
            batch,
            node_label="SqlAttribute",
            node_id=live_attr_id,
            name=attr.name,
            description=attr.description,
            sql=attr.sql,
            database_name=database_name,
            schema_cache=schema_cache,
            extra_props={"expression": attr.sql, "source": source},
        )
        link_lines.extend(writes.row_ops("sql_attribute__term", {"attribute_id": live_attr_id, "term_id": live_term_id}))
        if embed_buffer is not None:
            embed_buffer.semantic_rows.append(
                build_sql_attribute_semantic_row(
                    live_id=live_attr_id,
                    name=attr.name,
                    description=attr.description,
                    term_name=term_names.get(attr.term_id, ""),
                    sql=attr.sql,
                    database_name=database_name,
                ),
            )
    _run(batch.operations(plan_lines + link_lines))


def _import_custom_analyses(
    document: AutoOntologyModelDocument,
    id_map: dict[str, str],
    created: dict[str, int],
    skipped: dict[str, int],
    embed_buffer: ImportEmbedBuffer | None,
    schema_cache: dict,
) -> None:
    from auto_ontology.dal.model_interchange import ModelImportValidationError
    from auto_ontology.server.model_interchange.embed import build_custom_analysis_semantic_row

    analyses = document.semantic_layer.custom_analyses
    results, plan_lines = _plan_batch(
        "custom_analysis", [(a.id, {"name": a.name, "description": a.description}) for a in analyses]
    )
    for analysis in analyses:
        id_map[analysis.id] = results[analysis.id][0]

    live_column: dict[str, str | None] = {}
    lookup_ids: list[str] = []
    for analysis in analyses:
        _live_id, was_created = results[analysis.id]
        column_id: str | None = None
        if was_created and analysis.sql_column_is:
            try:
                column_id = _remap(id_map, analysis.sql_column_is[0], kind="custom analysis column")
            except ModelImportValidationError:
                column_id = None
        live_column[analysis.id] = column_id
        if column_id:
            lookup_ids.append(column_id)
    db_names = _database_names_for_columns(lookup_ids)

    batch = _SqlBatch(a.sql for a in analyses if results[a.id][1])
    for analysis in analyses:
        live_ca_id, was_created = results[analysis.id]
        if not was_created:
            skipped["custom_analyses"] += 1
            continue
        created["custom_analyses"] += 1
        column_id = live_column.get(analysis.id)
        database_name = db_names.get(column_id) if column_id else None
        _persist_sql_object(
            batch,
            node_label="CustomAnalysis",
            node_id=live_ca_id,
            name=analysis.name,
            description=analysis.description,
            sql=analysis.sql,
            database_name=database_name,
            schema_cache=schema_cache,
        )
        if embed_buffer is not None:
            embed_buffer.semantic_rows.append(
                build_custom_analysis_semantic_row(
                    live_id=live_ca_id,
                    name=analysis.name,
                    description=analysis.description,
                    sql=analysis.sql,
                    database_name=database_name,
                ),
            )
    _run(batch.operations(plan_lines))


def apply_import_model(
    document: AutoOntologyModelDocument,
    *,
    replace: bool,
    embed_buffer: ImportEmbedBuffer | None = None,
) -> dict[str, Any]:
    """Apply a validated model document (see the module docstring: a failed
    import is compensated, not rolled back)."""
    id_map: dict[str, str] = {}
    column_meta: dict[str, Any] = {}
    created: dict[str, int] = {
        "databases": 0,
        "schemas": 0,
        "tables": 0,
        "columns": 0,
        "terms": 0,
        "column_attributes": 0,
        "sql_attributes": 0,
        "custom_analyses": 0,
    }
    skipped: dict[str, int] = {key: 0 for key in created}
    term_names = {term.id: term.name for term in document.semantic_layer.terms}
    schema_cache: dict = {}

    _preflight(document)
    # Every stage is its own write; a failure in a later stage undoes the earlier
    # ones by one compensating write (created subjects retracted, changed ones set
    # back to their prior values). See the module docstring: "compensated".
    with writes.compensating():
        live_db_ids = _import_catalog(document, id_map, created, skipped, embed_buffer, column_meta)
        if replace:
            _delete_scoped_semantics_not_in_payload(document, live_db_ids)
        _import_foreign_keys(document, id_map)
        _import_joins(document, id_map)
        _import_terms(document, id_map, created, skipped, embed_buffer)
        _import_column_attributes(document, id_map, created, skipped, embed_buffer, column_meta)
        _import_semantic_fks(document, id_map)
        _import_sql_attributes(document, id_map, created, skipped, embed_buffer, term_names, schema_cache)
        _import_custom_analyses(document, id_map, created, skipped, embed_buffer, schema_cache)

    summary: dict[str, Any] = {
        "database_ids": live_db_ids,
        "created": created,
        "skipped": skipped,
        "replace": replace,
        "terms": len(document.semantic_layer.terms),
        "column_attributes": sum(len(term.columns_attributes) for term in document.semantic_layer.terms),
        "semantic_fks": len(document.semantic_layer.semantic_fks),
        "sql_attributes": sum(
            len(getattr(document.semantic_layer.sql_attributes, key)) for key in _YAML_SOURCE_KEYS
        ),
        "custom_analyses": len(document.semantic_layer.custom_analyses),
    }
    if embed_buffer is not None:
        summary["pending_embed"] = {
            "data_rows": len(embed_buffer.data_rows),
            "semantic_rows": len(embed_buffer.semantic_rows),
        }
    return summary
