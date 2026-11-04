# SPDX-License-Identifier: Apache-2.0
"""RDF ports of ``auto_ontology.dal.sql_attributes`` reads.

Also holds the helpers the ``custom_analyses`` and ``candidates`` ports share
(statement text, tables a statement references, column summaries, generic row
reads): they traverse the same ``*__sql`` / ``sql_query__table`` links.

Two things the Direct Mapping does not give for free are done in Python, as the
SQL did them in the database: the "lowest-id statement" collapse and the
all-or-nothing zone scope. ``zone_ids`` are resolved to table ids through
``auto_ontology.dal.users`` (looked up at call time, so whichever backend is
installed there answers).
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import JSON, ARRAY

from auto_ontology.dal import schema as s
from auto_ontology.dal_rdf._common import P, client, iri, values, wrap
from auto_ontology.rdf.vocab import class_iri, prop_iri, row_id, row_iri_single

logger = logging.getLogger(__name__)

PORTS = [
    "fetch_sql_attributes_by_term_id",
    "fetch_sql_attributes_with_sql",
    "fetch_tables_from_sql_attributes",
    "find_attr_by_name",
    "get_full_sql_attribute_by_id",
]

_RDF_JSON = "<http://www.w3.org/1999/02/22-rdf-syntax-ns#JSON>"


# ---------------------------------------------------------------------------
# Shared helpers (also used by dal_rdf.custom_analyses and dal_rdf.candidates)
# ---------------------------------------------------------------------------


def ids_values(var: str, table: str, ids) -> str:
    return values(var, [row_iri_single(table, i) for i in ids])


def read_rows(table_name: str, ids=None) -> dict[str, dict[str, Any]]:
    """``{id: {every column of the table: value-or-None}}``, i.e. ``SELECT *``.

    ``ids=None`` reads every row. Array and JSON columns are read from their
    single ``rdf:JSON`` literal (the member triples would multiply the rows).
    """
    table = s.METADATA.tables[table_name]
    cols = [c for c in table.columns if c.name != "id"]
    opts = []
    for i, c in enumerate(cols):
        flt = ""
        if isinstance(c.type, (ARRAY, JSON)):
            flt = f" FILTER(datatype(?c{i}) = {_RDF_JSON})"
        opts.append(f"OPTIONAL {{ ?s {iri(prop_iri(table_name, c.name))} ?c{i} .{flt} }}")
    pin = ids_values("s", table_name, ids) if ids is not None else ""
    body = f"{pin} ?s a {iri(class_iri(table_name))} . {' '.join(opts)}"
    sel = " ".join(f"?c{i}" for i in range(len(cols)))
    rows = client().select(f"SELECT ?s {sel} WHERE {{ {wrap(body)} }}")
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        rid = row_id(r["s"])
        row = {"id": rid}
        for i, c in enumerate(cols):
            row[c.name] = r.get(f"c{i}")
        out[rid] = row
    return out


def statement_texts(link: str, owner_col: str, owner_ids=None) -> dict[str, str]:
    """``{owner_id: sql_full_query of its lowest-id statement}``.

    Owners with no statement are absent, which is what ``_has_sql`` tests.
    """
    owner_table = {"sql_attribute__sql": "sql_attribute", "custom_analysis__sql": "custom_analysis"}[link]
    pin = ids_values("o", owner_table, owner_ids) if owner_ids is not None else ""
    ref = getattr(getattr(P, link), f"ref_{owner_col}")
    body = (
        f"{pin} ?l a {iri(class_iri(link))} ; {ref} ?o ; "
        f"{getattr(P, link).ref_sql_query_id} ?q . "
        f"?q {P.sql_query.sql_full_query} ?text"
    )
    best: dict[str, tuple[str, str]] = {}
    for r in client().select(f"SELECT ?o ?q ?text WHERE {{ {wrap(body)} }}"):
        o, q = row_id(r["o"]), row_id(r["q"])
        if o not in best or q < best[o][0]:
            best[o] = (q, r["text"])
    return {o: t for o, (_, t) in best.items()}


def owned_table_ids(link: str, owner_col: str, owner_ids) -> dict[str, list[str]]:
    """``{owner_id: sorted table ids its statements reference}``."""
    owner_table = {"sql_attribute__sql": "sql_attribute", "custom_analysis__sql": "custom_analysis"}[link]
    pin = ids_values("o", owner_table, owner_ids)
    ref = getattr(getattr(P, link), f"ref_{owner_col}")
    body = (
        f"{pin} ?l a {iri(class_iri(link))} ; {ref} ?o ; "
        f"{getattr(P, link).ref_sql_query_id} ?q . "
        f"?qt a {iri(class_iri('sql_query__table'))} ; "
        f"{P.sql_query__table.ref_sql_query_id} ?q ; {P.sql_query__table.ref_table_id} ?t"
    )
    owned: dict[str, set[str]] = {}
    for r in client().select(f"SELECT DISTINCT ?o ?t WHERE {{ {wrap(body)} }}"):
        owned.setdefault(row_id(r["o"]), set()).add(row_id(r["t"]))
    return {o: sorted(ts) for o, ts in owned.items()}


def _nonblank(value: str | None) -> str | None:
    """``nullif(trim(coalesce(x, '')), '')``: Postgres ``trim`` strips spaces only."""
    v = (value or "").strip(" ")
    return v or None


def column_descriptions(column_ids) -> dict[str, str | None]:
    """``column_description_expr()`` for each column id.

    Own description (trimmed) if non-blank; else the lowest-id ``HAS_ATTRIBUTE``
    attribute with a non-blank description (returned untrimmed); else the same
    over ``SEMANTIC_FK``.
    """
    column_ids = list(column_ids)
    if not column_ids:
        return {}
    own = read_columns_own_description(column_ids)
    via: dict[str, dict[str, list[tuple[str, str]]]] = {"column__has_attribute": {}, "column__semantic_fk": {}}
    for link in via:
        body = (
            f"{ids_values('c', 'catalog_column', column_ids)} "
            f"?l a {iri(class_iri(link))} ; {getattr(P, link).ref_column_id} ?c ; "
            f"{getattr(P, link).ref_attribute_id} ?a . "
            f"?a {P.column_attribute.description} ?d"
        )
        for r in client().select(f"SELECT ?c ?a ?d WHERE {{ {wrap(body)} }}"):
            if _nonblank(r["d"]) is not None:
                via[link].setdefault(row_id(r["c"]), []).append((row_id(r["a"]), r["d"]))
    out: dict[str, str | None] = {}
    for c in column_ids:
        d = _nonblank(own.get(c))
        if d is None:
            for link in ("column__has_attribute", "column__semantic_fk"):
                found = via[link].get(c)
                if found:
                    d = min(found)[1]
                    break
        out[c] = d
    return out


def read_columns_own_description(column_ids) -> dict[str, str]:
    body = (
        f"{ids_values('c', 'catalog_column', column_ids)} "
        f"?c {P.catalog_column.description} ?d"
    )
    return {row_id(r["c"]): r["d"] for r in client().select(f"SELECT ?c ?d WHERE {{ {wrap(body)} }}")}


def tables_from_statements(link: str, owner_col: str, owner_ids) -> list[dict[str, Any]]:
    """The body of ``fetch_tables_from_sql_attributes`` / ``..._custom_analyses``.

    Tables referenced by the owners' statements, with their columns, ordered by
    table id then ordinal position; tables with no columns do not appear
    (the SQL's join to ``catalog_column`` is inner).
    """
    owned = owned_table_ids(link, owner_col, owner_ids)
    table_ids = sorted({t for ts in owned.values() for t in ts})
    if not table_ids:
        return []
    body = (
        f"{ids_values('t', 'catalog_table', table_ids)} "
        f"?t {P.catalog_table.name} ?tname ; {P.catalog_table.ref_schema_id} ?sch . "
        f"OPTIONAL {{ ?t {P.catalog_table.description} ?tdesc }} "
        f"OPTIONAL {{ ?t {P.catalog_table.pk} ?pk . FILTER(datatype(?pk) = {_RDF_JSON}) }} "
        f"?sch {P.catalog_schema.name} ?sname ; {P.catalog_schema.ref_database_id} ?db . "
        f"?db {P.catalog_database.name} ?dname . "
        f"?c {P.catalog_column.ref_table_id} ?t ; {P.catalog_column.name} ?cname ; "
        f"   {P.catalog_column.ordinal_position} ?pos . "
        f"OPTIONAL {{ ?c {P.catalog_column.data_type} ?dtype }}"
    )
    rows = client().select(
        "SELECT ?t ?tname ?tdesc ?pk ?sname ?dname ?c ?cname ?dtype ?pos "
        f"WHERE {{ {wrap(body)} }}"
    )
    descs = column_descriptions({row_id(r["c"]) for r in rows})
    # DISTINCT on the SQL row; ORDER BY table id, ordinal_position.
    seen = set()
    flat = []
    for r in rows:
        cid = row_id(r["c"])
        key = (row_id(r["t"]), cid)
        if key in seen:
            continue
        seen.add(key)
        flat.append((row_id(r["t"]), r["pos"], cid, r))
    flat.sort(key=lambda x: (x[0], x[1], x[2]))
    tables: dict[str, dict[str, Any]] = {}
    for tid, _pos, cid, r in flat:
        table = tables.setdefault(
            tid,
            {
                "id": tid,
                "name": r.get("tname") or "",
                "description": r.get("tdesc") or "",
                "database_name": r.get("dname") or "",
                "schema_name": r.get("sname") or "",
                "label": "Table",
                "pk": r.get("pk") or [],
                "columns": [],
            },
        )
        if r.get("cname"):
            table["columns"].append(
                {"name": r["cname"], "data_type": r.get("dtype"), "description": descs.get(cid)}
            )
    return list(tables.values())


# ---------------------------------------------------------------------------
# Attribute rows
# ---------------------------------------------------------------------------

_ATTR_COLS = [
    "name", "description", "description_suggestion", "expression", "source",
    "certified", "owner_id", "created", "modified", "created_by", "modified_by",
]


def _attribute_rows(attr_ids=None, term_id=None) -> list[dict[str, Any]]:
    """``_attribute_select()`` rows: attribute + Term + SQL text, statement-less
    attributes excluded. Unordered; callers sort."""
    pin = ids_values("a", "sql_attribute", attr_ids) if attr_ids is not None else ""
    term_pin = f"VALUES ?term {{ {iri(row_iri_single('term', term_id))} }}" if term_id is not None else ""
    opts = " ".join(
        f"OPTIONAL {{ ?a {getattr(P.sql_attribute, c)} ?{c} }}" for c in _ATTR_COLS
    )
    body = (
        f"{pin} {term_pin} ?a a {iri(class_iri('sql_attribute'))} . {opts} "
        f"?l a {iri(class_iri('sql_attribute__term'))} ; "
        f"   {P.sql_attribute__term.ref_attribute_id} ?a ; {P.sql_attribute__term.ref_term_id} ?term . "
        f"?term {P.term.name} ?term_name"
    )
    sel = " ".join(f"?{c}" for c in _ATTR_COLS)
    rows = client().select(f"SELECT ?a ?term ?term_name {sel} WHERE {{ {wrap(body)} }}")
    if not rows:
        return []
    texts = statement_texts("sql_attribute__sql", "attribute_id", {row_id(r["a"]) for r in rows})
    out = []
    for r in rows:
        aid = row_id(r["a"])
        if aid not in texts:  # _has_sql
            continue
        row = {"id": aid}
        for c in ("name", "description", "description_suggestion", "expression", "source"):
            row[c] = r.get(c)
        row["sql"] = texts[aid]
        for c in _ATTR_COLS[5:]:
            row[c] = r.get(c)
        row["term_id"] = row_id(r["term"])
        row["term_name"] = r["term_name"]
        out.append(row)
    return out


def _zone_filtered(rows: list[dict[str, Any]], zone_ids) -> list[dict[str, Any]]:
    """All-or-nothing: drop attributes whose SQL touches a table outside the zones."""
    if zone_ids is None or not rows:
        return rows
    from auto_ontology.dal import users

    allowed = set(users.resolve_accessible_catalog_ids(zone_ids)["table_ids"])
    owned = owned_table_ids("sql_attribute__sql", "attribute_id", [r["id"] for r in rows])
    return [r for r in rows if all(t in allowed for t in owned.get(r["id"], []))]


def _query_sql_attributes(*, attr_id=None, term_id=None, zone_ids=None, key=None, skip=0, limit=None):
    if attr_id is not None:
        rows = _attribute_rows(attr_ids=[attr_id], term_id=term_id)
    else:
        rows = _attribute_rows(term_id=term_id)
    rows = _zone_filtered(rows, zone_ids)
    if key is not None:
        rows.sort(key=key)
    if skip:
        rows = rows[skip:]
    if limit is not None:
        rows = rows[:limit]
    return rows


def _tags_map(ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    from auto_ontology.dal import tags

    return tags.fetch_tags_map(tags.TARGET_SQL_ATTRIBUTE, ids)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def get_full_sql_attribute_by_id(attr_id: str, zone_ids: list[str] | None = None) -> dict[str, Any] | None:
    rows = _query_sql_attributes(attr_id=attr_id, zone_ids=zone_ids)
    if not rows:
        return None
    result = rows[0]

    body = (
        f"?al a {iri(class_iri('sql_attribute__sql'))} ; "
        f"   {P.sql_attribute__sql.ref_attribute_id} {iri(row_iri_single('sql_attribute', attr_id))} ; "
        f"   {P.sql_attribute__sql.ref_sql_query_id} ?q . "
        f"?qt a {iri(class_iri('sql_query__table'))} ; "
        f"   {P.sql_query__table.ref_sql_query_id} ?q ; {P.sql_query__table.ref_table_id} ?t . "
        f"?t {P.catalog_table.ref_schema_id} ?sch . "
        f"?sch {P.catalog_schema.ref_database_id} ?db . "
        f"?zt a {iri(class_iri('zone_target'))} ; {P.zone_target.ref_zone_id} ?z . "
        f"{{ ?zt {P.zone_target.ref_table_id} ?t }} UNION "
        f"{{ ?zt {P.zone_target.ref_schema_id} ?sch }} UNION "
        f"{{ ?zt {P.zone_target.ref_database_id} ?db }} "
        f"?z {P.zone.name} ?name ; {P.zone.enabled} ?enabled . "
        f"OPTIONAL {{ ?z {P.zone.color} ?color }}"
    )
    zrows = client().select(f"SELECT DISTINCT ?z ?name ?color ?enabled WHERE {{ {wrap(body)} }}")
    zones = [
        {"id": row_id(r["z"]), "name": r["name"], "color": r.get("color"), "enabled": r["enabled"]}
        for r in zrows
    ]
    if zone_ids is not None:
        wanted = set(zone_ids)
        zones = [z for z in zones if z["id"] in wanted and z["enabled"] is True]
    zones.sort(key=lambda z: (z["name"], z["id"]))
    result["zones"] = zones
    result["tags"] = _tags_map([attr_id]).get(attr_id, [])
    return result


def fetch_sql_attributes_by_term_id(
    term_id: str,
    zone_ids: list[str] | None = None,
    *,
    skip: int = 0,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    rows = _query_sql_attributes(
        term_id=term_id,
        zone_ids=zone_ids,
        key=lambda r: (r["name"], r["id"]),
        skip=skip,
        limit=limit,
    )
    tags = _tags_map([row["id"] for row in rows])
    return [{**row, "tags": tags.get(row["id"], [])} for row in rows]


def find_attr_by_name(name: str, exclude_id: str | None) -> dict[str, str] | None:
    body = f"?a {P.sql_attribute.name} ?n . FILTER(?n = {_lit(name)})"
    rows = client().select(f"SELECT ?a ?n WHERE {{ {wrap(body)} }}")
    found = sorted(
        (row_id(r["a"]), r["n"]) for r in rows if exclude_id is None or row_id(r["a"]) != exclude_id
    )
    return {"id": found[0][0], "name": found[0][1]} if found else None


def _lit(value: str) -> str:
    from auto_ontology.rdf.client import lit

    return lit(value)


def fetch_sql_attributes_with_sql(attr_ids: list[str]) -> list[dict[str, str]]:
    if not attr_ids:
        return []
    try:
        rows = _attribute_rows(attr_ids=list(attr_ids))
    except Exception:
        logger.warning("fetch_sql_attributes_with_sql: query failed", exc_info=True)
        return []
    rows.sort(key=lambda r: (r["id"], r["term_id"]))
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        attr_id = row["id"] or ""
        if attr_id in seen:
            continue
        seen.add(attr_id)
        result.append(
            {
                "id": attr_id,
                "name": row["name"] or "",
                "description": row["description"] or "",
                "expression": row["expression"] or "",
                "sql": row["sql"] or "",
                "term_name": row["term_name"] or "",
            }
        )
    return result


def fetch_tables_from_sql_attributes(attr_ids: list[str]) -> list[dict[str, Any]]:
    if not attr_ids:
        return []
    try:
        return tables_from_statements("sql_attribute__sql", "attribute_id", list(attr_ids))
    except Exception:
        logger.warning("fetch_tables_from_sql_attributes: query failed", exc_info=True)
        return []
