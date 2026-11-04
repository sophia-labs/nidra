# SPDX-License-Identifier: Apache-2.0
"""RDF ports of ``auto_ontology.dal.datasources`` reads (lane datasources-a).

Each function keeps the upstream name, signature, return shape and edge
behaviour; the SQL in ``auto_ontology/dal/datasources.py`` is the spec.

Two pieces of SQL semantics worth knowing when reading this file:

* The description fallbacks (``sql_fragments``) return the *trimmed* own
  description (Postgres ``trim`` strips spaces only) and the *raw* attribute /
  term description, picking the lowest id among non-blank candidates.
* Postgres sorts NULLs last on an ascending ``ORDER BY``; ``_nulls_last`` does
  the same for ``ordinal_position``.

Zone scoping goes through ``auto_ontology.dal.users.resolve_accessible_catalog_ids``
looked up at call time, so it follows whatever backend that module has.
"""
from __future__ import annotations

import logging
from typing import Any

from auto_ontology.dal_rdf._common import P, client, iri, lit, values, wrap
from auto_ontology.rdf.vocab import class_iri, row_id, row_iri_single

logger = logging.getLogger(__name__)

PORTS = [
    "fetch_databases",
    "fetch_schemas_for_database",
    "fetch_all_schema_ids",
    "fetch_schemas_by_ids",
    "fetch_tables_for_schema",
    "fetch_tables_by_ids",
    "fetch_table_by_id",
    "fetch_table_by_name",
    "find_table_id_by_name",
    "fetch_table_context",
]

_JSON = "http://www.w3.org/1999/02/22-rdf-syntax-ns#JSON"


def _resolve_scope(zone_ids: list[str] | None):
    from auto_ontology.dal import users

    return users.resolve_accessible_catalog_ids(zone_ids)


def _select(body: str, select: str, tail: str = "") -> list[dict[str, Any]]:
    return client().select(f"SELECT {select} WHERE {{ {wrap(body)} }} {tail}")


def _cls(table: str) -> str:
    return iri(class_iri(table))


def _pk_json(var: str, subject: str) -> str:
    """OPTIONAL binding the JSON-typed ``pk`` array of a table into ``?var``."""
    return (
        f"OPTIONAL {{ {subject} {P.catalog_table.pk} ?{var} . "
        f"FILTER(datatype(?{var}) = <{_JSON}>) }}"
    )


def _blank(value: Any) -> bool:
    return value is None or str(value).strip(" ") == ""


def _trim(value: Any) -> str | None:
    """``nullif(trim(coalesce(v, '')), '')``."""
    if value is None:
        return None
    stripped = str(value).strip(" ")
    return stripped or None


def _nulls_last(value: Any) -> tuple[int, Any]:
    return (1, 0) if value is None else (0, value)


def _chunks(items: list[str], size: int = 2000):
    for i in range(0, len(items), size):
        yield items[i : i + size]


# ---------------------------------------------------------------------------
# Database / schema
# ---------------------------------------------------------------------------


def fetch_databases(zone_ids: list[str] | None = None) -> list[dict[str, Any]]:
    """Databases with a schema count. ``schemas`` is empty — the tree is lazy."""
    scoped = _resolve_scope(zone_ids)
    body = (
        f"?db a {_cls('catalog_database')} ; {P.catalog_database.name} ?name . "
        f"OPTIONAL {{ ?db {P.catalog_database.owner_id} ?owner_id }} "
        f"?schema {P.catalog_schema.ref_database_id} ?db . "
    )
    if scoped is not None:
        if not scoped["db_ids"] or not scoped["schema_ids"]:
            return []
        body += (
            values("db", [row_iri_single("catalog_database", i) for i in sorted(scoped["db_ids"])])
            + " "
            + values("schema", [row_iri_single("catalog_schema", i) for i in sorted(scoped["schema_ids"])])
        )
    rows = _select(
        body,
        "?db ?name ?owner_id (COUNT(?schema) AS ?schema_count)",
        "GROUP BY ?db ?name ?owner_id ORDER BY ?name ?db",
    )
    return [
        {
            "id": row_id(r["db"]),
            "name": r["name"],
            "description": None,
            "owner_id": r.get("owner_id"),
            "num_of_schemas": int(r["schema_count"]),
            "schemas": [],
        }
        for r in rows
    ]


def fetch_schemas_for_database(
    db_id: str,
    zone_ids: list[str] | None = None,
) -> dict[str, Any] | None:
    """``{schemas_count, schemas}`` for a database, or ``None`` if it has none."""
    scoped = _resolve_scope(zone_ids)
    body = (
        f"?schema {P.catalog_schema.ref_database_id} {iri(row_iri_single('catalog_database', db_id))} ; "
        f"        {P.catalog_schema.name} ?schema_name . "
        f"OPTIONAL {{ ?schema {P.catalog_schema.owner_id} ?owner_id }} "
        f"?table {P.catalog_table.ref_schema_id} ?schema . "
    )
    if scoped is not None:
        if not scoped["schema_ids"] or not scoped["table_ids"]:
            return None
        body += (
            values("schema", [row_iri_single("catalog_schema", i) for i in sorted(scoped["schema_ids"])])
            + " "
            + values("table", [row_iri_single("catalog_table", i) for i in sorted(scoped["table_ids"])])
        )
    rows = _select(
        body,
        "?schema ?schema_name ?owner_id (COUNT(?table) AS ?tables_count)",
        "GROUP BY ?schema ?schema_name ?owner_id ORDER BY ?schema_name ?schema",
    )
    if not rows:
        return None
    return {
        "schemas_count": len(rows),
        "schemas": [
            {
                "id": row_id(r["schema"]),
                "schema_name": r["schema_name"],
                "description": None,
                "owner_id": r.get("owner_id"),
                "tables_count": int(r["tables_count"]),
            }
            for r in rows
        ],
    }


def fetch_all_schema_ids() -> list[str]:
    rows = _select(f"?s a {_cls('catalog_schema')} .", "?s")
    return [row_id(r["s"]) for r in rows]


def fetch_schemas_by_ids(
    relevant_schemas_ids: list | None = None,
) -> list[dict[str, str]]:
    """Flat column rows for the catalog map SQL validation is built from.

    An empty or absent id list means *every* schema, not none.
    """
    schema_ids = relevant_schemas_ids or []
    body = (
        f"?col a {_cls('catalog_column')} ; {P.catalog_column.name} ?column_name ; "
        f"     {P.catalog_column.ref_table_id} ?tbl . "
        f"OPTIONAL {{ ?col {P.catalog_column.data_type} ?data_type }} "
        f"?tbl {P.catalog_table.name} ?table_name ; {P.catalog_table.ref_schema_id} ?sch . "
        f"?sch {P.catalog_schema.name} ?table_schema ; {P.catalog_schema.ref_database_id} ?db . "
        f"?db {P.catalog_database.name} ?database_name . "
    )
    if schema_ids:
        body += values("sch", [row_iri_single("catalog_schema", i) for i in schema_ids])
    rows = _select(
        body,
        "?col ?column_name ?tbl ?table_name ?database_name ?table_schema ?data_type",
    )
    return [
        {
            "column_name": r["column_name"],
            "column_id": row_id(r["col"]),
            "table_name": r["table_name"],
            "table_id": row_id(r["tbl"]),
            "database_name": r["database_name"],
            "table_schema": r["table_schema"],
            "data_type": r.get("data_type"),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Table
# ---------------------------------------------------------------------------


def _table_rows(extra: str, tail: str = "") -> list[dict[str, Any]]:
    body = (
        f"?t a {_cls('catalog_table')} ; {P.catalog_table.name} ?name ; "
        f"   {P.catalog_table.ref_schema_id} ?sch . "
        f"?sch {P.catalog_schema.name} ?schema_name . "
        f"OPTIONAL {{ ?t {P.catalog_table.description} ?description }} "
        f"{_pk_json('pk', '?t')} {extra}"
    )
    rows = _select(body, "?t ?name ?schema_name ?description ?pk", tail)
    return [
        {
            "id": row_id(r["t"]),
            "name": r["name"],
            "schema_name": r["schema_name"],
            "description": r.get("description"),
            "pk": r.get("pk"),
        }
        for r in rows
    ]


def fetch_table_by_id(table_id: str) -> dict[str, Any] | None:
    rows = _table_rows(f"FILTER(?t = {iri(row_iri_single('catalog_table', table_id))})")
    return rows[0] if rows else None


def fetch_table_by_name(name: str) -> dict[str, Any] | None:
    """The first table of that name, in *any* schema or database (lowest id)."""
    rows = _table_rows(f"FILTER(?name = {lit(name)})")
    return min(rows, key=lambda r: r["id"]) if rows else None


def find_table_id_by_name(
    table_name: str, database_name: str | None = None
) -> str | None:
    """Resolve a bare table name (from a join-path hop) to its Table id."""
    if not table_name:
        return None
    body = (
        f"?t a {_cls('catalog_table')} ; {P.catalog_table.name} ?name . "
        f"FILTER(LCASE(?name) = {lit(table_name.lower())}) "
    )
    if database_name:
        body += (
            f"?t {P.catalog_table.ref_schema_id} ?sch . "
            f"?sch {P.catalog_schema.ref_database_id} ?db . "
            f"?db {P.catalog_database.name} {lit(database_name)} . "
        )
    ids = sorted(row_id(r["t"]) for r in _select(body, "DISTINCT ?t"))
    if not database_name and len(ids) > 1:
        logger.info(
            "find_table_id_by_name: ambiguous match for %s with no "
            "database_name given — treating as unresolved",
            table_name,
        )
        return None
    return ids[0] if ids else None


def _attribute_descriptions(link_table: str, column_iris: list[str]) -> dict[str, str]:
    """``{column_id: description}`` — first non-blank (lowest attribute id) via *link_table*."""
    found: dict[str, tuple[str, str]] = {}
    for chunk in _chunks(column_iris):
        rows = _select(
            f"{values('col', chunk)} "
            f"?link {getattr(P, link_table).ref_column_id} ?col ; {getattr(P, link_table).ref_attribute_id} ?attr . "
            f"?attr {P.column_attribute.description} ?desc . ",
            "?col ?attr ?desc",
        )
        for r in rows:
            if _blank(r["desc"]):
                continue
            col, attr_id = row_id(r["col"]), row_id(r["attr"])
            if col not in found or attr_id < found[col][0]:
                found[col] = (attr_id, r["desc"])
    return {c: d for c, (_, d) in found.items()}


def _column_descriptions(columns: list[dict[str, Any]]) -> dict[str, str | None]:
    """``column_description_expr`` for rows carrying ``id`` and ``description``."""
    iris = [row_iri_single("catalog_column", c["id"]) for c in columns]
    has_attr = _attribute_descriptions("column__has_attribute", iris)
    sem_fk = _attribute_descriptions("column__semantic_fk", iris)
    out: dict[str, str | None] = {}
    for c in columns:
        out[c["id"]] = _trim(c.get("description")) or has_attr.get(c["id"]) or sem_fk.get(c["id"])
    return out


def _table_descriptions(tables: list[dict[str, Any]]) -> dict[str, str | None]:
    """``table_description_expr`` for rows carrying ``id`` and ``description``."""
    from_term: dict[str, tuple[str, str]] = {}
    for chunk in _chunks([row_iri_single("catalog_table", t["id"]) for t in tables]):
        rows = _select(
            f"{values('t', chunk)} "
            f"?link {P.table__term.ref_table_id} ?t ; {P.table__term.ref_term_id} ?term . "
            f"?term {P.term.description} ?desc . ",
            "?t ?term ?desc",
        )
        for r in rows:
            if _blank(r["desc"]):
                continue
            tid, term_id = row_id(r["t"]), row_id(r["term"])
            if tid not in from_term or term_id < from_term[tid][0]:
                from_term[tid] = (term_id, r["desc"])
    return {
        t["id"]: _trim(t.get("description")) or (from_term[t["id"]][1] if t["id"] in from_term else None)
        for t in tables
    }


def _count_by_table(cls_table: str, ref_col: str, table_iris: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for chunk in _chunks(table_iris):
        rows = _select(
            f"{values('t', chunk)} ?x {getattr(getattr(P, cls_table), ref_col)} ?t . ",
            "?t (COUNT(?x) AS ?n)",
            "GROUP BY ?t",
        )
        counts.update({row_id(r["t"]): int(r["n"]) for r in rows})
    return counts


def _terms_counts(table_iris: list[str]) -> dict[str, int]:
    """Distinct Terms per table: directly (REPRESENTS) or through a column's attribute."""
    counts: dict[str, int] = {}
    for chunk in _chunks(table_iris):
        via = lambda link: (  # noqa: E731
            f"{{ ?col {P.catalog_column.ref_table_id} ?t . "
            f"?l {getattr(P, link).ref_column_id} ?col ; {getattr(P, link).ref_attribute_id} ?attr . "
            f"?cat {P.column_attribute__term.ref_attribute_id} ?attr ; "
            f"      {P.column_attribute__term.ref_term_id} ?term . }}"
        )
        direct = (
            f"{{ ?d {P.table__term.ref_table_id} ?t ; {P.table__term.ref_term_id} ?term . }}"
        )
        rows = _select(
            f"{values('t', chunk)} ?term a {_cls('term')} . "
            f"{direct} UNION {via('column__has_attribute')} UNION {via('column__semantic_fk')}",
            "?t (COUNT(DISTINCT ?term) AS ?n)",
            "GROUP BY ?t",
        )
        counts.update({row_id(r["t"]): int(r["n"]) for r in rows})
    return counts


def _tags_for_tables(table_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    """``fetch_tags_map(TARGET_TABLE, ids)``: ordered by lower(name), id."""
    tags: dict[str, list[dict[str, Any]]] = {}
    for chunk in _chunks([row_iri_single("catalog_table", i) for i in table_ids]):
        rows = _select(
            f"{values('t', chunk)} "
            f"?tt {P.tag_target.ref_table_id} ?t ; {P.tag_target.ref_tag_id} ?tag . "
            f"?tag {P.tag.name} ?tag_name . ",
            "?t ?tag ?tag_name",
        )
        for r in sorted(rows, key=lambda r: (r["tag_name"].lower(), row_id(r["tag"]))):
            tags.setdefault(row_id(r["t"]), []).append({"id": row_id(r["tag"]), "name": r["tag_name"]})
    return tags


def fetch_tables_for_schema(
    schema_id: str,
    *,
    database_name: str | None = None,
    zone_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Tables in a schema, with column, SQL, and Term counts.

    *database_name* is accepted and ignored, as upstream does.
    """
    scoped = _resolve_scope(zone_ids)
    body = (
        f"?t {P.catalog_table.ref_schema_id} {iri(row_iri_single('catalog_schema', schema_id))} ; "
        f"   {P.catalog_table.name} ?name . "
        f"?sch {P.catalog_schema.name} ?schema_name ; {P.catalog_schema.ref_database_id} ?db . "
        f"FILTER(?sch = {iri(row_iri_single('catalog_schema', schema_id))}) "
        f"?db {P.catalog_database.name} ?database_name . "
        f"OPTIONAL {{ ?t {P.catalog_table.table_type} ?table_type }} "
        f"OPTIONAL {{ ?t {P.catalog_table.description} ?description }} "
        f"OPTIONAL {{ ?t {P.catalog_table.description_certified} ?description_certified }} "
        f"OPTIONAL {{ ?t {P.catalog_table.owner_id} ?owner_id }} "
    )
    found = _select(
        body,
        "?t ?name ?table_type ?database_name ?schema_name ?description "
        "?description_certified ?owner_id",
        "ORDER BY ?name ?t",
    )
    if scoped is not None:
        allowed = scoped["table_ids"]
        found = [r for r in found if row_id(r["t"]) in allowed]

    ids = [row_id(r["t"]) for r in found]
    iris = [row_iri_single("catalog_table", i) for i in ids]
    descs = _table_descriptions(
        [{"id": row_id(r["t"]), "description": r.get("description")} for r in found]
    )
    columns = _count_by_table("catalog_column", "ref_table_id", iris)
    sqls = _count_by_table("sql_query__table", "ref_table_id", iris)
    terms = _terms_counts(iris)
    tags = _tags_for_tables(ids)

    return [
        {
            "id": row_id(r["t"]),
            "name": r["name"],
            "table_type": r.get("table_type"),
            "database_name": r["database_name"],
            "schema_name": r["schema_name"],
            "description": descs[row_id(r["t"])],
            "description_certified": r.get("description_certified"),
            "owner_id": r.get("owner_id"),
            "columns_count": columns.get(row_id(r["t"]), 0),
            "sql_count": sqls.get(row_id(r["t"]), 0),
            "terms_count": terms.get(row_id(r["t"]), 0),
            "tags": tags.get(row_id(r["t"]), []),
        }
        for r in found
    ]


def _semantic_fk_targets(column_iris: list[str]) -> dict[str, str]:
    """``{column_id: 'schema.table.column'}`` — the least target by (schema, table, column)."""
    best: dict[str, tuple[str, str, str]] = {}
    for chunk in _chunks(column_iris):
        rows = _select(
            f"{values('col', chunk)} "
            f"?fk {P.column__semantic_fk.ref_column_id} ?col ; {P.column__semantic_fk.ref_attribute_id} ?attr . "
            f"?own {P.column__has_attribute.ref_attribute_id} ?attr ; {P.column__has_attribute.ref_column_id} ?tc . "
            f"?tc {P.catalog_column.name} ?c_name ; {P.catalog_column.ref_table_id} ?tt . "
            f"?tt {P.catalog_table.name} ?t_name ; {P.catalog_table.ref_schema_id} ?ts . "
            f"?ts {P.catalog_schema.name} ?s_name . ",
            "?col ?s_name ?t_name ?c_name",
        )
        for r in rows:
            key = (r["s_name"], r["t_name"], r["c_name"])
            col = row_id(r["col"])
            if col not in best or key < best[col]:
                best[col] = key
    return {c: ".".join(k) for c, k in best.items()}


def _columns_of(table_iris: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for chunk in _chunks(table_iris):
        rows.extend(
            _select(
                f"{values('t', chunk)} "
                f"?col {P.catalog_column.ref_table_id} ?t ; {P.catalog_column.name} ?column_name . "
                f"OPTIONAL {{ ?col {P.catalog_column.ordinal_position} ?ordinal_position }} "
                f"OPTIONAL {{ ?col {P.catalog_column.data_type} ?data_type }} "
                f"OPTIONAL {{ ?col {P.catalog_column.description} ?description }} "
                f"OPTIONAL {{ ?col {P.catalog_column.format} ?format }} "
                f"OPTIONAL {{ ?col {P.catalog_column.sample_values} ?sample_values }} "
                f"OPTIONAL {{ ?col {P.catalog_column.is_nullable} ?is_nullable }}",
                "?t ?col ?column_name ?ordinal_position ?data_type ?description ?format "
                "?sample_values ?is_nullable",
            )
        )
    return rows


def fetch_tables_by_ids(table_ids: list[str]) -> list[dict[str, Any]]:
    """Tables with a name/type/description/sample_values summary of each column.

    ``[]`` if the query fails. A table with no columns does not appear.
    """
    if not table_ids:
        return []
    from auto_ontology.utils.sample_values import parse_sample_values

    try:
        wanted = list(dict.fromkeys(table_ids))
        iris = [row_iri_single("catalog_table", i) for i in wanted]
        tables: dict[str, dict[str, Any]] = {}
        for chunk in _chunks(iris):
            for r in _select(
                f"{values('t', chunk)} ?t a {_cls('catalog_table')} ; "
                f"   {P.catalog_table.name} ?name ; {P.catalog_table.ref_schema_id} ?sch . "
                f"?sch {P.catalog_schema.name} ?schema_name ; {P.catalog_schema.ref_database_id} ?db . "
                f"?db {P.catalog_database.name} ?database_name . "
                f"OPTIONAL {{ ?t {P.catalog_table.description} ?description }} "
                f"{_pk_json('pk', '?t')}",
                "?t ?name ?description ?pk ?database_name ?schema_name",
            ):
                tables[row_id(r["t"])] = r

        cols = _columns_of(iris)
        col_rows = [
            {**c, "id": row_id(c["col"]), "table": row_id(c["t"])} for c in cols if row_id(c["t"]) in tables
        ]
        descs = _column_descriptions(col_rows)
        fk_targets = _semantic_fk_targets([row_iri_single("catalog_column", c["id"]) for c in col_rows])
    except Exception:
        logger.warning("fetch_tables_by_ids: query failed", exc_info=True)
        return []

    by_table: dict[str, list[dict[str, Any]]] = {}
    for c in col_rows:
        by_table.setdefault(c["table"], []).append(c)

    result: list[dict[str, Any]] = []
    for tid in sorted(tables):
        columns = by_table.get(tid)
        if not columns:
            continue  # inner join on column: a column-less table is absent
        t = tables[tid]
        out_cols = []
        for c in sorted(columns, key=lambda c: (_nulls_last(c.get("ordinal_position")), c["id"])):
            out_cols.append(
                {
                    "name": c["column_name"],
                    "data_type": c.get("data_type"),
                    "description": descs[c["id"]],
                    "format": c.get("format"),
                    "sample_values": parse_sample_values(c.get("sample_values")),
                    "is_nullable": c.get("is_nullable"),
                    "semantic_fk": fk_targets.get(c["id"]),
                }
            )
        result.append(
            {
                "id": tid,
                "name": t["name"] or "",
                "description": t.get("description") or "",
                "database_name": t["database_name"] or "",
                "schema_name": t["schema_name"] or "",
                "label": "Table",
                "pk": t.get("pk") or [],
                "columns": out_cols,
            }
        )
    return result


def fetch_table_context(table_id: str) -> dict[str, Any]:
    """``{columns, fks}`` for one table — what the SQL generator is handed."""
    t_iri = iri(row_iri_single("catalog_table", table_id))
    rows = client().select(
        "SELECT ?col ?column_name ?data_type ?description ?ordinal_position ?sample_values ?format "
        "(EXISTS { "
        + wrap(f"?fk {P.column__foreign_key.ref_target_column_id} ?col")
        + " } AS ?is_fk_target) WHERE { "
        + wrap(
            f"?col {P.catalog_column.ref_table_id} {t_iri} ; {P.catalog_column.name} ?column_name . "
            f"OPTIONAL {{ ?col {P.catalog_column.ordinal_position} ?ordinal_position }} "
            f"OPTIONAL {{ ?col {P.catalog_column.data_type} ?data_type }} "
            f"OPTIONAL {{ ?col {P.catalog_column.description} ?description }} "
            f"OPTIONAL {{ ?col {P.catalog_column.format} ?format }} "
            f"OPTIONAL {{ ?col {P.catalog_column.sample_values} ?sample_values }}"
        )
        + " }"
    )
    cols = [{**r, "id": row_id(r["col"])} for r in rows]
    descs = _column_descriptions(cols)
    columns = [
        {
            "id": c["id"],
            "name": c["column_name"],
            "data_type": c.get("data_type"),
            "description": descs[c["id"]],
            "ordinal_position": c.get("ordinal_position"),
            "sample_values": c.get("sample_values"),
            "format": c.get("format"),
            "is_foreign_key_target": bool(c.get("is_fk_target")),
        }
        for c in sorted(cols, key=lambda c: (_nulls_last(c.get("ordinal_position")), c["id"]))
    ]

    fk_rows = _select(
        f"?src {P.catalog_column.ref_table_id} {t_iri} ; {P.catalog_column.name} ?source_column . "
        f"?fk {P.column__foreign_key.ref_source_column_id} ?src ; "
        f"    {P.column__foreign_key.ref_target_column_id} ?tgt . "
        f"?tgt {P.catalog_column.name} ?target_column ; {P.catalog_column.ref_table_id} ?tt . "
        f"?tt {P.catalog_table.name} ?target_table . ",
        "?source_column ?target_column ?target_table ?tt",
    )
    fks = [
        {
            "source_column": r["source_column"],
            "target_column": r["target_column"],
            "target_table": r["target_table"],
            "target_table_id": row_id(r["tt"]),
        }
        for r in fk_rows
    ]
    return {"columns": columns, "fks": fks}
