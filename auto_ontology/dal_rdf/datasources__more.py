# SPDX-License-Identifier: Apache-2.0
"""RDF ports of the remaining ``auto_ontology.dal.datasources`` reads.

``patch.py`` maps ``datasources__more`` onto ``auto_ontology.dal.datasources``;
the existing ports in ``datasources.py`` / ``datasources__b.py`` are untouched.
"""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from auto_ontology.dal_rdf._common import P, class_iri, client, iri, lit, row_id, row_iri_single, values, wrap
from auto_ontology.dal_rdf.sql_attributes import column_descriptions
from auto_ontology.rdf.client import RDF_JSON
from auto_ontology.utils.sample_values import stringify_sample_values

logger = logging.getLogger(__name__)

PORTS = [
    "fetch_all_tables_without_term",
    "fetch_bridge_table_candidates",
    "fetch_col_table_contexts",
    "fetch_join_edges",
    "fetch_schema_ids_for_database",
    "fetch_sorted_tables",
    "fetch_tables_and_columns_by_node_ids",
]


def _cls(table: str) -> str:
    return iri(class_iri(table))


def _sel(vars_: str, body: str, tail: str = "") -> list[dict[str, Any]]:
    return client().select(f"SELECT {vars_} WHERE {{ {wrap(body)} }} {tail}")


def _json_opt(var: str, subject: str, prop: str) -> str:
    return f"OPTIONAL {{ {subject} {prop} ?{var} . FILTER(datatype(?{var}) = <{RDF_JSON}>) }}"


def _chunks(items: list[str], size: int = 2000):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def fetch_all_tables_without_term(database_name: str | None = None) -> list[dict[str, Any]]:
    """Tables with no ``table__term`` row, by name (the table's own description)."""
    body = (
        f"?t a {_cls('catalog_table')} ; {P.catalog_table.name} ?name ; {P.catalog_table.ref_schema_id} ?sch . "
        f"?sch {P.catalog_schema.name} ?schema_name ; {P.catalog_schema.ref_database_id} ?db . "
        f"?db {P.catalog_database.name} ?database_name . "
        f"OPTIONAL {{ ?t {P.catalog_table.description} ?description }} "
        f"FILTER NOT EXISTS {{ ?tt a {_cls('table__term')} ; {P.table__term.ref_table_id} ?t }}"
    )
    if database_name is not None:
        body += f" FILTER(?database_name = {lit(database_name)})"
    rows = _sel("?t ?name ?description ?schema_name", body)
    out = [
        {
            "id": row_id(r["t"]),
            "name": r["name"],
            "description": r.get("description"),
            "schema_name": r["schema_name"],
        }
        for r in rows
    ]
    out.sort(key=lambda r: (r["name"], r["id"]))
    return out


def fetch_schema_ids_for_database(database_name: str) -> list[str]:
    body = (
        f"?sch a {_cls('catalog_schema')} ; {P.catalog_schema.ref_database_id} ?db . "
        f"?db {P.catalog_database.name} {lit(database_name)}"
    )
    return sorted(row_id(r["sch"]) for r in _sel("?sch", body))


def fetch_sorted_tables() -> list[dict[str, Any]]:
    """Every table (with a schema), busiest first, ``name`` then id breaking ties."""
    counts: dict[str, int] = {}
    cbody = f"?q a {_cls('sql_query__table')} ; {P.sql_query__table.ref_table_id} ?t"
    for r in _sel("?t (COUNT(?q) AS ?n)", cbody, "GROUP BY ?t"):
        counts[r["t"]] = int(r["n"])
    body = (
        f"?t a {_cls('catalog_table')} ; {P.catalog_table.name} ?name ; {P.catalog_table.ref_schema_id} ?sch . "
        f"?sch {P.catalog_schema.name} ?schema_name . "
        f"OPTIONAL {{ ?t {P.catalog_table.description} ?description }} "
        + _json_opt("pk", "?t", P.catalog_table.pk)
    )
    rows = _sel("?t ?name ?schema_name ?description ?pk", body)
    out = [
        {
            "id": row_id(r["t"]),
            "name": r["name"],
            "schema_name": r["schema_name"],
            "description": r.get("description") or "",
            "query_count": counts.get(r["t"], 0),
            "pk": r.get("pk") or [],
        }
        for r in rows
    ]
    out.sort(key=lambda r: (-r["query_count"], r["name"], r["id"]))
    return out


def fetch_join_edges() -> list[dict[str, Any]]:
    body = (
        f"?j a {_cls('table__join')} ; {P.table__join.ref_source_table_id} ?s ; "
        f"{P.table__join.ref_target_table_id} ?t . "
        f"?s {P.catalog_table.name} ?sn . ?t {P.catalog_table.name} ?tn . "
        + _json_opt("jc", "?j", P.table__join.join_columns)
    )
    rows = _sel("?j ?s ?sn ?t ?tn ?jc", body)
    rows.sort(key=lambda r: r["j"])
    return [
        {
            "source_table": r["sn"],
            "source_table_id": row_id(r["s"]),
            "target_table": r["tn"],
            "target_table_id": row_id(r["t"]),
            "join_columns": r.get("jc"),
        }
        for r in rows
    ]


def fetch_col_table_contexts(col_ids: list[str]) -> dict[str, dict[str, str]]:
    if not col_ids:
        return {}
    try:
        out: dict[str, dict[str, str]] = {}
        for chunk in _chunks(list(col_ids)):
            body = (
                f"{values('c', [row_iri_single('catalog_column', i) for i in chunk])} "
                f"?c {P.catalog_column.ref_table_id} ?t . "
                f"?t {P.catalog_table.name} ?tn ; {P.catalog_table.ref_schema_id} ?sch . "
                f"?sch {P.catalog_schema.name} ?sn ; {P.catalog_schema.ref_database_id} ?db . "
                f"?db {P.catalog_database.name} ?dn"
            )
            for r in _sel("?c ?t ?tn ?sch ?sn ?db ?dn", body):
                out[row_id(r["c"])] = {
                    "table_id": row_id(r["t"]) or "",
                    "table_name": r.get("tn") or "",
                    "schema_id": row_id(r["sch"]) or "",
                    "schema_name": r.get("sn") or "",
                    "database_id": row_id(r["db"]) or "",
                    "database_name": r.get("dn") or "",
                }
        return out
    except Exception:
        logger.warning("fetch_col_table_contexts: query failed", exc_info=True)
        return {}


def fetch_tables_and_columns_by_node_ids(node_ids: list[str]) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    ids = list(node_ids)
    tables_rows: list[dict[str, Any]] = []
    columns_rows: dict[str, dict[str, Any]] = {}
    for chunk in _chunks(ids):
        # Tables named by id (in the id list) ...
        tbody = (
            f"{values('t', [row_iri_single('catalog_table', i) for i in chunk])} "
            f"?t a {_cls('catalog_table')} ; {P.catalog_table.name} ?tn ; {P.catalog_table.ref_schema_id} ?sch . "
            f"?sch {P.catalog_schema.name} ?ts ; {P.catalog_schema.ref_database_id} ?db . "
            f"?db {P.catalog_database.name} ?dn . "
            f"OPTIONAL {{ ?t {P.catalog_table.table_type} ?tt }} "
            f"OPTIONAL {{ ?t {P.catalog_table.description} ?desc }}"
        )
        for r in _sel("?t ?tn ?ts ?tt ?desc ?dn", tbody):
            tables_rows.append(
                {
                    "id": row_id(r["t"]),
                    "table_name": r["tn"],
                    "table_schema": r["ts"],
                    "table_type": r.get("tt"),
                    "description": r.get("desc"),
                    "database_name": r["dn"],
                }
            )
        # ... and columns whose own id or whose table's id is in the list.
        for pin in (
            f"{values('c', [row_iri_single('catalog_column', i) for i in chunk])} ?c {P.catalog_column.ref_table_id} ?t .",
            f"{values('t', [row_iri_single('catalog_table', i) for i in chunk])} ?c {P.catalog_column.ref_table_id} ?t .",
        ):
            cbody = (
                f"{pin} ?c {P.catalog_column.name} ?cn . "
                f"?t {P.catalog_table.name} ?tn ; {P.catalog_table.ref_schema_id} ?sch . "
                f"?sch {P.catalog_schema.name} ?ts ; {P.catalog_schema.ref_database_id} ?db . "
                f"?db {P.catalog_database.name} ?dn . "
                f"OPTIONAL {{ ?c {P.catalog_column.data_type} ?dt }} "
                f"OPTIONAL {{ ?c {P.catalog_column.sample_values} ?sv }}"
            )
            for r in _sel("?c ?cn ?tn ?ts ?dn ?dt ?sv", cbody):
                columns_rows[row_id(r["c"])] = {
                    "id": row_id(r["c"]),
                    "table_name": r["tn"],
                    "table_schema": r["ts"],
                    "column_name": r["cn"],
                    "data_type": r.get("dt"),
                    "sample_values": stringify_sample_values(r.get("sv")),
                    "database_name": r["dn"],
                }
    desc = column_descriptions(list(columns_rows))
    col_list = [
        {
            "id": c["id"],
            "table_name": c["table_name"],
            "table_schema": c["table_schema"],
            "column_name": c["column_name"],
            "data_type": c["data_type"],
            "description": desc.get(cid),
            "sample_values": c["sample_values"],
            "database_name": c["database_name"],
        }
        for cid, c in sorted(columns_rows.items())
    ]
    seen: set[str] = set()
    table_list = []
    for t in tables_rows:
        if t["id"] not in seen:
            seen.add(t["id"])
            table_list.append(t)
    table_list.sort(key=lambda t: t["id"])
    columns_df = pd.DataFrame(col_list)
    tables_df = pd.DataFrame(table_list)

    database_name = ""
    for frame in (tables_df, columns_df):
        if not frame.empty:
            database_name = str(frame.iloc[0].get("database_name") or "")
            break
    return tables_df, columns_df, database_name


def fetch_bridge_table_candidates(database_name: str) -> list[dict[str, Any]]:
    """Pure-FK junction tables eligible for a bridge SqlAttribute (see the DAL docstring)."""
    # Tables of the database, with column counts.
    tbody = (
        f"?t a {_cls('catalog_table')} ; {P.catalog_table.name} ?tn ; {P.catalog_table.ref_schema_id} ?sch . "
        f"?sch {P.catalog_schema.name} ?sn ; {P.catalog_schema.ref_database_id} ?db . "
        f"?db {P.catalog_database.name} {lit(database_name)} . "
        f"OPTIONAL {{ ?t {P.catalog_table.description} ?desc }}"
    )
    tables = {
        r["t"]: r for r in _sel("?t ?tn ?sn ?desc", tbody)
    }
    if not tables:
        return []
    cols: dict[str, list[str]] = {}  # table iri -> column iris
    for chunk in _chunks(sorted(tables)):
        cbody = f"{values('t', chunk)} ?c {P.catalog_column.ref_table_id} ?t"
        for r in _sel("?c ?t", cbody):
            cols.setdefault(r["t"], []).append(r["c"])
    candidates = [t for t in tables if len(cols.get(t, [])) >= 2]
    if not candidates:
        return []

    all_cols = [c for t in candidates for c in cols[t]]
    col_table = {c: t for t in candidates for c in cols[t]}

    def link_rows(link: str, ref: str, extra_var: str) -> list[dict[str, Any]]:
        out = []
        for chunk in _chunks(all_cols):
            body = f"{values('c', chunk)} ?l a {_cls(link)} ; {ref} ?c ; {extra_var}"
            out.extend(_sel("?c ?x", body))
        return out

    # Columns carrying a ColumnAttribute of their own disqualify the table.
    has_attr: set[str] = set()
    for chunk in _chunks(all_cols):
        body = f"{values('c', chunk)} ?l a {_cls('column__has_attribute')} ; {P.column__has_attribute.ref_column_id} ?c"
        has_attr |= {col_table[r["c"]] for r in _sel("?c", body)}

    # Column -> FK targets (real, then semantic), each target a column that sits inside a table.
    targets: dict[str, set[str]] = {c: set() for c in all_cols}
    fk_targets: dict[str, set[str]] = {c: set() for c in all_cols}
    for r in link_rows("column__foreign_key", P.column__foreign_key.ref_source_column_id,
                       f"{P.column__foreign_key.ref_target_column_id} ?x"):
        fk_targets[r["c"]].add(r["x"])
    sem_attrs: dict[str, set[str]] = {}
    for r in link_rows("column__semantic_fk", P.column__semantic_fk.ref_column_id,
                       f"{P.column__semantic_fk.ref_attribute_id} ?x"):
        sem_attrs.setdefault(r["c"], set()).add(r["x"])
    owners: dict[str, set[str]] = {}
    attrs = sorted({a for s_ in sem_attrs.values() for a in s_})
    for chunk in _chunks(attrs):
        body = f"{values('x', chunk)} ?l a {_cls('column__has_attribute')} ; {P.column__has_attribute.ref_attribute_id} ?x ; {P.column__has_attribute.ref_column_id} ?o"
        for r in _sel("?x ?o", body):
            owners.setdefault(r["x"], set()).add(r["o"])
    sem_targets: dict[str, set[str]] = {c: set() for c in all_cols}
    for c, attr_set in sem_attrs.items():
        for a in attr_set:
            sem_targets[c] |= owners.get(a, set())
    targets = {c: fk_targets[c] | sem_targets[c] for c in all_cols}

    # Resolve targets to (table, schema, column) — only targets inside a table with a schema count.
    all_targets = sorted({t for ts in targets.values() for t in ts})
    info: dict[str, dict[str, Any]] = {}
    for chunk in _chunks(all_targets):
        body = (
            f"{values('x', chunk)} ?x {P.catalog_column.name} ?xn ; {P.catalog_column.ref_table_id} ?xt . "
            f"?xt {P.catalog_table.name} ?xtn . "
            f"OPTIONAL {{ ?xt {P.catalog_table.ref_schema_id} ?xs . ?xs {P.catalog_schema.name} ?xsn }}"
        )
        for r in _sel("?x ?xn ?xt ?xtn ?xsn", body):
            info[r["x"]] = r

    # `resolved` = distinct rows (column, target table/schema/column names, target table id).
    resolved: dict[str, set[tuple[str, str | None, str, str]]] = {}
    for c in all_cols:
        # Outer join of FK targets x semantic targets; coalesce prefers the FK side.
        for f in fk_targets[c] or {None}:
            for x in sem_targets[c] or {None}:
                pick = f if f in info else (x if x in info else None)
                if pick is not None:
                    r = info[pick]
                    resolved.setdefault(c, set()).add((r["xtn"], r.get("xsn"), r["xn"], row_id(r["xt"])))

    # Tables already bridged by a bridge-source SqlAttribute.
    bridged: set[str] = set()
    bbody = (
        f"?a a {_cls('sql_attribute')} ; {P.sql_attribute.source} \"bridgeTable\" . "
        f"?al a {_cls('sql_attribute__sql')} ; {P.sql_attribute__sql.ref_attribute_id} ?a ; "
        f"{P.sql_attribute__sql.ref_sql_query_id} ?q . "
        f"?qt a {_cls('sql_query__table')} ; {P.sql_query__table.ref_sql_query_id} ?q ; "
        f"{P.sql_query__table.ref_table_id} ?t"
    )
    bridged = {r["t"] for r in _sel("?t", bbody)}

    out: list[dict[str, Any]] = []
    for t in candidates:
        if t in has_attr or t in bridged:
            continue
        tcols = cols[t]
        rows_per_col = [resolved.get(c, set()) for c in tcols]
        n = len(tcols)
        if sum(1 for rs in rows_per_col if rs) != n:  # every column resolves
            continue
        if sum(len(rs) for rs in rows_per_col) != n:  # none resolves twice
            continue
        pairs = []
        for c in tcols:
            for tt, ts, tc, tid in resolved[c]:
                pairs.append(
                    {
                        "source_column": None,  # filled below
                        "target_table": tt,
                        "target_schema": ts,
                        "target_column": tc,
                        "target_table_id": tid,
                        "_col": c,
                    }
                )
        out.append({"t": t, "pairs": pairs})

    if not out:
        return []
    src_names: dict[str, str] = {}
    cs = sorted({p["_col"] for o in out for p in o["pairs"]})
    for chunk in _chunks(cs):
        for r in _sel("?c ?n", f"{values('c', chunk)} ?c {P.catalog_column.name} ?n"):
            src_names[r["c"]] = r["n"]

    result = []
    for o in sorted(out, key=lambda o: (tables[o["t"]]["tn"], o["t"])):
        t = tables[o["t"]]
        for p in o["pairs"]:
            p["source_column"] = src_names.get(p.pop("_col"))
        result.append(
            {
                "table_id": row_id(o["t"]),
                "table_name": t["tn"],
                "schema_name": t["sn"],
                "description": t.get("desc"),
                "fk_pairs": o["pairs"],
            }
        )
    return result
