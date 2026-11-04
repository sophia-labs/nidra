# SPDX-License-Identifier: Apache-2.0
"""RDF ports of the graph-traversal reads of ``auto_ontology.dal.attributes``.

Semantic foreign keys, join paths, hubs, bridges and junction tables. The
upstream SQL is the spec. Where it is a set query (EXISTS, UNION, DISTINCT) the
port is one SPARQL SELECT; where it is a breadth-first search over table edges
(``_find_table_join_hops``) the edge set comes from one SPARQL SELECT and the
BFS runs in Python unchanged, so its tie-breaks are identical.

Helpers local to this module (``_column_contexts``, ``_name_columns``,
``_name_tables``, ``_table_database_id``, ``_find_table_join_hops``,
``_find_table_bridge``) are private ports of the upstream helpers they stand
for: ``fetch_col_table_contexts`` and ``find_table_bridge`` belong to other
lanes, and ``find_kept_table_bridges`` must not fall back to SQL through them.
"""
from __future__ import annotations

from collections import defaultdict, deque
import itertools
import logging
from typing import Any

from auto_ontology.dal_rdf._common import P, client, iri, row_id, row_iri_single, values, wrap

logger = logging.getLogger(__name__)

PORTS = [
    "column_participates_in_semantic_fk",
    "fetch_attr_column_contexts",
    "find_anchor_hub_siblings",
    "find_connected_junction_tables",
    "find_join_path",
    "find_kept_table_bridges",
    "find_shared_hub_bridge",
]

#: Same ceiling as upstream (edges); a hop is four edges.
MAX_PATH_DEPTH = 30

_RDF_JSON = "http://www.w3.org/1999/02/22-rdf-syntax-ns#JSON"
_XSD_STRING = "http://www.w3.org/2001/XMLSchema#string"


def _sel(sparql: str) -> list[dict[str, Any]]:
    return client().select(sparql)


def _id_of(value: Any) -> str | None:
    return row_id(value) if value else None


def _pk_member(table_var: str, member_var: str) -> str:
    """Pattern binding one primary-key member (plain-string triple) of a table."""
    return (
        f"{table_var} {P.catalog_table.pk} {member_var} . "
        f"FILTER(datatype({member_var}) = <{_XSD_STRING}>)"
    )


# ---------------------------------------------------------------------------
# Private ports of the helpers upstream shares with other modules
# ---------------------------------------------------------------------------


def _column_contexts(col_ids: list[str]) -> dict[str, dict[str, str]]:
    """Port of ``datasources.fetch_col_table_contexts`` (plus the column name
    under ``column_name``). ``{}`` on failure, ids absent from the catalog omitted."""
    if not col_ids:
        return {}
    body = (
        f"{values('col', [row_iri_single('catalog_column', c) for c in col_ids])} "
        f"?col {P.catalog_column.name} ?column_name ; {P.catalog_column.ref_table_id} ?table . "
        f"?table {P.catalog_table.name} ?table_name ; {P.catalog_table.ref_schema_id} ?schema . "
        f"?schema {P.catalog_schema.name} ?schema_name ; {P.catalog_schema.ref_database_id} ?db . "
        f"?db {P.catalog_database.name} ?database_name ."
    )
    try:
        rows = _sel(
            "SELECT ?col ?column_name ?table ?table_name ?schema ?schema_name ?db ?database_name "
            f"WHERE {{ {wrap(body)} }}"
        )
    except Exception:
        logger.warning("fetch_col_table_contexts: query failed", exc_info=True)
        return {}
    return {
        row_id(r["col"]): {
            "column_name": r.get("column_name") or "",
            "table_id": row_id(r["table"]),
            "table_name": r.get("table_name") or "",
            "schema_id": row_id(r["schema"]),
            "schema_name": r.get("schema_name") or "",
            "database_id": row_id(r["db"]),
            "database_name": r.get("database_name") or "",
        }
        for r in rows
    }


def _name_columns(node_ids: list[str]) -> dict[str, str]:
    if not node_ids:
        return {}
    body = (
        f"{values('col', [row_iri_single('catalog_column', c) for c in node_ids])} "
        f"?col {P.catalog_column.name} ?name ."
    )
    return {row_id(r["col"]): r["name"] for r in _sel(f"SELECT ?col ?name WHERE {{ {wrap(body)} }}")}


def _name_tables(node_ids: list[str]) -> dict[str, str]:
    if not node_ids:
        return {}
    body = (
        f"{values('t', [row_iri_single('catalog_table', t) for t in node_ids])} "
        f"?t {P.catalog_table.name} ?name ."
    )
    return {row_id(r["t"]): r["name"] for r in _sel(f"SELECT ?t ?name WHERE {{ {wrap(body)} }}")}


def _table_database_id(table_id: str) -> str | None:
    body = (
        f"{values('t', [row_iri_single('catalog_table', table_id)])} "
        f"?t {P.catalog_table.ref_schema_id} ?s . ?s {P.catalog_schema.ref_database_id} ?db ."
    )
    rows = _sel(f"SELECT ?db WHERE {{ {wrap(body)} }}")
    return row_id(rows[0]["db"]) if rows else None


# ---------------------------------------------------------------------------
# Semantic FK membership and attribute contexts
# ---------------------------------------------------------------------------


def column_participates_in_semantic_fk(col_id: str) -> bool:
    """Whether *col_id* is already known to the FK graph, on either side."""
    if not col_id:
        return False
    col = iri(row_iri_single("catalog_column", col_id))
    body = (
        f"{{ ?l1 {P.column__semantic_fk.ref_column_id} {col} }} UNION "
        f"{{ ?h {P.column__has_attribute.ref_column_id} {col} ; "
        f"{P.column__has_attribute.ref_attribute_id} ?a . "
        f"?l2 {P.column__semantic_fk.ref_attribute_id} ?a }}"
    )
    return bool(_sel(f"SELECT * WHERE {{ {wrap(body)} }} LIMIT 1"))


def fetch_attr_column_contexts(
    attr_ids: list[str],
    *,
    database_name: str | None,
) -> dict[str, dict]:
    """Catalog context for each attribute id, keyed by that id (the owning column wins)."""
    if not attr_ids:
        return {}
    attr_iris = [row_iri_single("column_attribute", a) for a in attr_ids]
    try:
        attrs = _sel(
            "SELECT ?attr ?name ?description ?datatype WHERE { "
            + wrap(
                f"{values('attr', attr_iris)} ?attr {P.column_attribute.name} ?name . "
                f"OPTIONAL {{ ?attr {P.column_attribute.description} ?description }} "
                f"OPTIONAL {{ ?attr {P.column_attribute.datatype} ?datatype }}"
            )
            + " }"
        )
        links = _sel(
            "SELECT DISTINCT ?attr ?col ?rank WHERE { "
            + wrap(
                f"{values('attr', attr_iris)} "
                f"{{ ?l {P.column__has_attribute.ref_attribute_id} ?attr ; "
                f"{P.column__has_attribute.ref_column_id} ?col . BIND(0 AS ?rank) }} UNION "
                f"{{ ?l {P.column__semantic_fk.ref_attribute_id} ?attr ; "
                f"{P.column__semantic_fk.ref_column_id} ?col . BIND(1 AS ?rank) }}"
            )
            + " }"
        )
        terms = _sel(
            "SELECT ?attr ?term ?term_name WHERE { "
            + wrap(
                f"{values('attr', attr_iris)} "
                f"?l {P.column_attribute__term.ref_attribute_id} ?attr ; "
                f"{P.column_attribute__term.ref_term_id} ?term . "
                f"?term {P.term.name} ?term_name ."
            )
            + " }"
        )
    except Exception:
        logger.warning("fetch_attr_column_contexts: query failed", exc_info=True)
        return {}

    # ORDER BY attr id, rank, column id: the first link per attribute wins.
    best: dict[str, tuple[int, str]] = {}
    for link in links:
        key = (link["rank"], row_id(link["col"]))
        attr = row_id(link["attr"])
        if attr not in best or key < best[attr]:
            best[attr] = key
    try:
        contexts = _column_contexts([c for _, c in best.values()])
    except Exception:
        logger.warning("fetch_attr_column_contexts: query failed", exc_info=True)
        return {}

    term_by_attr: dict[str, tuple[str, str]] = {}
    for t in terms:  # several terms: SQL leaves the pick unordered; take the lowest id
        attr, key = row_id(t["attr"]), (row_id(t["term"]), t["term_name"])
        if attr not in term_by_attr or key < term_by_attr[attr]:
            term_by_attr[attr] = key

    result: dict[str, dict] = {}
    for row in sorted(attrs, key=lambda r: row_id(r["attr"])):
        attr_id = row_id(row["attr"])
        col_id = best[attr_id][1] if attr_id in best else None
        ctx = contexts.get(col_id) if col_id else None
        in_db = ctx is not None and (database_name is None or ctx["database_name"] == database_name)
        result.setdefault(
            attr_id,
            {
                "attr_name": row.get("name") or "",
                "attr_description": row.get("description") or "",
                "col_id": col_id if ctx is not None else None,
                "col_name": ctx["column_name"] if ctx else "",
                "table_id": ctx["table_id"] if ctx else None,
                "table_name": ctx["table_name"] if ctx else "",
                "schema_name": ctx["schema_name"] if ctx else "",
                "database_name": ctx["database_name"] if in_db else "",
                "term_name": (term_by_attr.get(attr_id) or (None, ""))[1] or "",
                "datatype": row.get("datatype") or "",
            },
        )
    return result


# ---------------------------------------------------------------------------
# Junction tables
# ---------------------------------------------------------------------------


def find_connected_junction_tables(
    table_ids: list[str],
) -> tuple[list[dict], list[list[dict]]]:
    """Junction tables with a one-hop FK (declared or semantic) into any supplied table."""
    kept_ids = list(dict.fromkeys(table_id for table_id in table_ids if table_id))
    if not kept_ids:
        return [], []

    common = (
        f"{values('tt', [row_iri_single('catalog_table', t) for t in kept_ids])} "
        f"?jc {P.catalog_column.ref_table_id} ?jt . "
        f"?jt {P.catalog_table.name} ?jname ; {P.catalog_table.is_junction_table} ?isj ; "
        f"{P.catalog_table.ref_schema_id} ?js . "
        f"?js {P.catalog_schema.ref_database_id} ?db . "
        f"?tc {P.catalog_column.ref_table_id} ?tt . "
        f"?tt {P.catalog_table.ref_schema_id} ?ts . "
        f"?ts {P.catalog_schema.ref_database_id} ?db . "
        f"FILTER(?isj = true && ?jt != ?tt) "
    )
    declared = (
        f"?fk {P.column__foreign_key.ref_source_column_id} ?jc ; "
        f"{P.column__foreign_key.ref_target_column_id} ?tc . "
    )
    semantic = (
        f"?sf {P.column__semantic_fk.ref_column_id} ?jc ; "
        f"{P.column__semantic_fk.ref_attribute_id} ?attr ; "
        f"{P.column__semantic_fk.use_in_join_paths} ?use . FILTER(?use = true) "
        f"?own {P.column__has_attribute.ref_attribute_id} ?attr ; "
        f"{P.column__has_attribute.ref_column_id} ?tc . "
    )
    sparql = (
        "SELECT DISTINCT ?jt ?jname ?jc ?tc WHERE { "
        + wrap(f"{{ {declared} {common} }} UNION {{ {semantic} {common} }}")
        + " }"
    )
    try:
        raw = _sel(sparql)
    except Exception:
        logger.warning(
            "find_connected_junction_tables: query failed for %d table(s)",
            len(kept_ids),
            exc_info=True,
        )
        return [], []
    rows = [
        {
            "junction_table_id": row_id(r["jt"]),
            "junction_table_name": r.get("jname"),
            "source_column_id": row_id(r["jc"]),
            "target_column_id": row_id(r["tc"]),
        }
        for r in raw
    ]
    rows.sort(
        key=lambda row: (
            row["junction_table_name"] or "",
            row["junction_table_id"] or "",
            row["source_column_id"] or "",
            row["target_column_id"] or "",
        )
    )
    column_ids = list(
        dict.fromkeys(
            column_id
            for row in rows
            for column_id in (row["source_column_id"], row["target_column_id"])
        )
    )
    names = _name_columns(column_ids)
    contexts = _column_contexts(column_ids)
    junctions: dict[str, dict] = {}
    paths: list[list[dict]] = []
    for row in rows:
        junction_id = row["junction_table_id"]
        source_id = row["source_column_id"]
        target_id = row["target_column_id"]
        source = contexts.get(source_id, {})
        target = contexts.get(target_id, {})
        if not junction_id or not source or not target:
            continue
        junctions.setdefault(
            junction_id,
            {"id": junction_id, "name": row["junction_table_name"] or ""},
        )
        paths.append([_hop(source, source_id, target, target_id, names, with_database=True)])
    return list(junctions.values()), paths


def _hop(
    source: dict, source_id: str, target: dict, target_id: str, names: dict, *, with_database: bool
) -> dict:
    hop = {}
    if with_database:
        hop["source_database"] = source.get("database_name", "")
    hop["source_schema"] = source.get("schema_name", "")
    hop["source_table"] = source.get("table_name", "")
    hop["source_column"] = names.get(source_id, "")
    if with_database:
        hop["target_database"] = target.get("database_name", "")
    hop["target_schema"] = target.get("schema_name", "")
    hop["target_table"] = target.get("table_name", "")
    hop["target_column"] = names.get(target_id, "")
    return hop


# ---------------------------------------------------------------------------
# Join paths
# ---------------------------------------------------------------------------


def _find_table_join_hops(
    anchor_table_id: str,
    dest_table_id: str,
    database_id: str,
) -> list[tuple[str, str]]:
    """Table-to-table joins, oriented anchor -> destination (BFS over semantic edges).

    The edge set is the SEMANTIC_FK rows (use_in_join_paths) whose FK column and
    owning column both sit in *database_id*; the BFS is upstream's, line for line.
    """
    db = iri(row_iri_single("catalog_database", database_id))
    body = (
        f"?sf {P.column__semantic_fk.ref_column_id} ?fkc ; "
        f"{P.column__semantic_fk.ref_attribute_id} ?attr ; "
        f"{P.column__semantic_fk.use_in_join_paths} ?use . FILTER(?use = true) "
        f"?fkc {P.catalog_column.ref_table_id} ?fkt . "
        f"?fkt {P.catalog_table.ref_schema_id} ?fks . ?fks {P.catalog_schema.ref_database_id} {db} . "
        f"?own {P.column__has_attribute.ref_attribute_id} ?attr ; "
        f"{P.column__has_attribute.ref_column_id} ?oc . "
        f"?oc {P.catalog_column.name} ?ocname ; {P.catalog_column.ref_table_id} ?ot . "
        f"?ot {P.catalog_table.ref_schema_id} ?os . ?os {P.catalog_schema.ref_database_id} {db} . "
        f"OPTIONAL {{ ?ot {P.catalog_table.pk} ?pk . FILTER(datatype(?pk) = <{_RDF_JSON}>) }}"
    )
    rows = _sel(f"SELECT DISTINCT ?fkt ?fkc ?ot ?oc ?ocname ?pk WHERE {{ {wrap(body)} }}")
    adjacency: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    seen: set[tuple] = set()
    for row in rows:
        fk_table_id, fk_column_id = row_id(row["fkt"]), row_id(row["fkc"])
        owner_table_id, owner_column_id = row_id(row["ot"]), row_id(row["oc"])
        owner_is_pk = row["ocname"] in (row.get("pk") or [])
        key = (fk_table_id, fk_column_id, owner_table_id, owner_column_id, owner_is_pk)
        if key in seen:
            continue
        seen.add(key)
        if fk_table_id == owner_table_id:
            continue
        adjacency[fk_table_id].append((owner_table_id, fk_column_id, owner_column_id))
        if owner_is_pk:
            adjacency[owner_table_id].append((fk_table_id, owner_column_id, fk_column_id))
    for edges in adjacency.values():
        edges.sort()

    max_hops = (MAX_PATH_DEPTH + 2) // 4
    parents: dict[str, tuple[str, str, str] | None] = {anchor_table_id: None}
    frontier: deque[tuple[str, int]] = deque([(anchor_table_id, 0)])
    while frontier:
        table_id, depth = frontier.popleft()
        if depth >= max_hops:
            continue
        for next_table_id, source_col_id, target_col_id in adjacency.get(table_id, []):
            if next_table_id in parents:
                continue
            parents[next_table_id] = (table_id, source_col_id, target_col_id)
            if next_table_id == dest_table_id:
                hops: list[tuple[str, str]] = []
                current = dest_table_id
                while parents[current] is not None:
                    previous, source_col_id, target_col_id = parents[current]
                    hops.append((source_col_id, target_col_id))
                    current = previous
                hops.reverse()
                return hops
            frontier.append((next_table_id, depth + 1))
    return []


def find_join_path(anchor_col_id: str, dest_col_id: str) -> list[dict]:
    """The shortest semantic join route between the columns' tables."""
    if anchor_col_id == dest_col_id:
        return []

    endpoint_contexts = _column_contexts([anchor_col_id, dest_col_id])
    anchor_context = endpoint_contexts.get(anchor_col_id)
    dest_context = endpoint_contexts.get(dest_col_id)
    if not anchor_context or not dest_context:
        return []
    if anchor_context["table_id"] == dest_context["table_id"]:
        return []
    if anchor_context["database_id"] != dest_context["database_id"]:
        logger.warning(
            "find_join_path: rejected cross-database path %s -> %s",
            anchor_col_id,
            dest_col_id,
        )
        return []

    try:
        hop_pairs = _find_table_join_hops(
            anchor_context["table_id"],
            dest_context["table_id"],
            anchor_context["database_id"],
        )
    except Exception:
        logger.warning(
            "find_join_path: query failed for %s -> %s",
            anchor_col_id,
            dest_col_id,
            exc_info=True,
        )
        return []

    if not hop_pairs:
        return []

    col_ids = list(dict.fromkeys(col_id for pair in hop_pairs for col_id in pair))
    names = _name_columns(col_ids)
    contexts = _column_contexts(col_ids)
    return [
        _hop(contexts.get(s, {}), s, contexts.get(t, {}), t, names, with_database=True)
        for s, t in hop_pairs
    ]


# ---------------------------------------------------------------------------
# Hubs
# ---------------------------------------------------------------------------


def find_shared_hub_bridge(col_a_id: str, col_b_id: str) -> dict:
    """A PK-anchored identity hub shared by two FK columns, as ``{hub_table, hub_column}``."""
    if col_a_id == col_b_id:
        return {}
    a = iri(row_iri_single("catalog_column", col_a_id))
    b = iri(row_iri_single("catalog_column", col_b_id))
    body = (
        f"?fa {P.column__semantic_fk.ref_column_id} {a} ; "
        f"{P.column__semantic_fk.ref_attribute_id} ?attr ; "
        f"{P.column__semantic_fk.use_in_join_paths} ?ua . FILTER(?ua = true) "
        f"?fb {P.column__semantic_fk.ref_column_id} {b} ; "
        f"{P.column__semantic_fk.ref_attribute_id} ?attr ; "
        f"{P.column__semantic_fk.use_in_join_paths} ?ub . FILTER(?ub = true) "
        f"?own {P.column__has_attribute.ref_attribute_id} ?attr ; "
        f"{P.column__has_attribute.ref_column_id} ?hc . "
        f"?hc {P.catalog_column.name} ?hub_column ; {P.catalog_column.ref_table_id} ?ht . "
        f"?ht {P.catalog_table.name} ?hub_table . "
        f"{_pk_member('?ht', '?pkm')} FILTER(?pkm = ?hub_column)"
    )
    try:
        rows = _sel(
            f"SELECT ?hc ?hub_table ?hub_column WHERE {{ {wrap(body)} }} ORDER BY ?hc LIMIT 1"
        )
    except Exception:
        logger.warning(
            "find_shared_hub_bridge: query failed for %s / %s",
            col_a_id,
            col_b_id,
            exc_info=True,
        )
        return {}
    if not rows:
        return {}
    return {
        "hub_table": rows[0]["hub_table"] or "",
        "hub_column": rows[0]["hub_column"] or "",
    }


def find_anchor_hub_siblings(
    anchor_table_id: str, max_siblings: int | None = 6
) -> tuple[list[dict], int]:
    """Tables sharing a PK-anchored hub with *anchor_table_id* via its outgoing SEMANTIC_FKs."""
    anchor = iri(row_iri_single("catalog_table", anchor_table_id))
    body = (
        f"?fk {P.catalog_column.ref_table_id} {anchor} . "
        f"{anchor} {P.catalog_table.name} ?anchor_table . "
        f"?sf {P.column__semantic_fk.ref_column_id} ?fk ; "
        f"{P.column__semantic_fk.ref_attribute_id} ?attr ; "
        f"{P.column__semantic_fk.use_in_join_paths} ?u1 . FILTER(?u1 = true) "
        f"?own {P.column__has_attribute.ref_attribute_id} ?attr ; "
        f"{P.column__has_attribute.ref_column_id} ?hc . "
        f"?hc {P.catalog_column.name} ?hcname ; {P.catalog_column.ref_table_id} ?hub . "
        f"?hub {P.catalog_table.name} ?hub_table . "
        f"?sib_fk {P.column__semantic_fk.ref_attribute_id} ?attr ; "
        f"{P.column__semantic_fk.ref_column_id} ?sc ; "
        f"{P.column__semantic_fk.use_in_join_paths} ?u2 . FILTER(?u2 = true) "
        f"?sc {P.catalog_column.ref_table_id} ?sib . "
        f"?sib {P.catalog_table.name} ?sibling_table . "
        f"{_pk_member('?hub', '?pkm')} FILTER(?pkm = ?hcname && ?sib != {anchor})"
    )
    try:
        raw = _sel(
            "SELECT DISTINCT ?sib ?sibling_table ?hub ?hub_table ?anchor_table "
            f"WHERE {{ {wrap(body)} }}"
        )
    except Exception:
        logger.warning(
            "find_anchor_hub_siblings: query failed for table %s",
            anchor_table_id,
            exc_info=True,
        )
        return [], 0

    # SQL returns DISTINCT rows in no defined order; fix one (select-list order,
    # which is what a sort-based DISTINCT yields) so the cap is deterministic.
    rows = sorted(
        (
            {
                "sibling_id": row_id(r["sib"]),
                "sibling_table": r.get("sibling_table"),
                "hub_id": row_id(r["hub"]),
                "hub_table": r.get("hub_table"),
                "anchor_table": r.get("anchor_table"),
            }
            for r in raw
        ),
        key=lambda r: (r["sibling_id"], r["sibling_table"] or "", r["hub_id"], r["hub_table"] or ""),
    )

    anchor_name = ""
    hubs: dict[str, str] = {}  # hub_id -> hub_name
    siblings_by_hub: dict[str, list[dict]] = {}
    for row in rows:
        anchor_name = row["anchor_table"] or anchor_name
        hub_id, hub_name = row["hub_id"], row["hub_table"]
        if not hub_id:
            continue
        hubs[hub_id] = hub_name or ""
        sib_id, sib_name = row["sibling_id"], row["sibling_table"]
        if sib_id:
            siblings_by_hub.setdefault(hub_id, []).append(
                {"id": sib_id, "name": sib_name or ""}
            )

    results: list[dict] = []
    truncated = 0
    for hub_id, hub_name in hubs.items():
        results.append(
            {
                "source_table": anchor_name,
                "target_table": hub_name,
                "id": hub_id,
                "hub_table": hub_name,
                "is_hub": True,
            }
        )
        sibs = siblings_by_hub.get(hub_id, [])
        if max_siblings is not None and len(sibs) > max_siblings:
            truncated += len(sibs) - max_siblings
            sibs = sibs[:max_siblings]
        for sib in sibs:
            results.append(
                {
                    "source_table": anchor_name,
                    "target_table": sib["name"],
                    "id": sib["id"],
                    "hub_table": hub_name,
                    "is_hub": False,
                }
            )
    return results, truncated


# ---------------------------------------------------------------------------
# Bridges
# ---------------------------------------------------------------------------


def _find_table_bridge(
    table_a_id: str,
    table_b_id: str,
    allowed_table_ids: set[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Private port of ``find_table_bridge`` (``find_kept_table_bridges`` depends on it)."""
    for src, dst in ((table_a_id, table_b_id), (table_b_id, table_a_id)):
        database_id = _table_database_id(src)
        if not database_id:
            continue
        try:
            hop_pairs = _find_table_join_hops(src, dst, database_id)
        except Exception:
            logger.warning(
                "find_table_bridge: query failed for %s -> %s", src, dst, exc_info=True
            )
            continue
        if not hop_pairs:
            continue

        col_ids = list(dict.fromkeys(cid for pair in hop_pairs for cid in pair))
        names = _name_columns(col_ids)
        col_ctx = _column_contexts(col_ids)

        bridge_ids = list(
            dict.fromkeys(
                col_ctx.get(target_id, {}).get("table_id")
                for _, target_id in hop_pairs[:-1]
            )
        )
        bridge_ids = [tid for tid in bridge_ids if tid]
        if not bridge_ids:
            continue
        if allowed_table_ids is not None and not set(bridge_ids).issubset(allowed_table_ids):
            logger.info(
                "find_table_bridge: discarding path %s -> %s — bridge table(s) "
                "were never a candidate, not just restoring a dropped one",
                src,
                dst,
            )
            continue

        hops = [
            _hop(col_ctx.get(s, {}), s, col_ctx.get(t, {}), t, names, with_database=False)
            for s, t in hop_pairs
        ]
        table_names = _name_tables(bridge_ids)
        bridge_tables = [{"id": tid, "name": table_names.get(tid, "")} for tid in bridge_ids]
        return bridge_tables, hops
    return [], []


def find_kept_table_bridges(
    table_ids: list[str],
    allowed_table_ids: set[str] | None = None,
    max_bridge_tables: int = 5,
) -> tuple[list[dict], list[list[dict]], int]:
    """Bridge tables and their join hops connecting pairs of already-kept tables."""
    if len(table_ids) < 2:
        return [], [], 0

    already_kept = set(table_ids)
    pairs = list(itertools.combinations(table_ids, 2))
    found: dict[str, dict] = {}
    bridge_paths: list[list[dict]] = []
    checked = 0
    for a_id, b_id in pairs:
        if len(found) >= max_bridge_tables:
            break
        checked += 1
        bridge_tables, hops = _find_table_bridge(a_id, b_id, allowed_table_ids)
        for t in bridge_tables:
            if t["id"] in already_kept:
                continue
            found.setdefault(t["id"], t)
        if bridge_tables and hops:
            bridge_paths.append(hops)

    skipped = len(pairs) - checked
    if skipped:
        logger.info(
            "find_kept_table_bridges: cap of %d bridge table(s) hit — "
            "skipped %d/%d remaining pair(s)",
            max_bridge_tables,
            skipped,
            len(pairs),
        )
    return list(found.values()), bridge_paths, skipped
