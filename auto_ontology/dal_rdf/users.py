# SPDX-License-Identifier: Apache-2.0
"""RDF ports of the zone-scope helpers of ``auto_ontology.dal.users``.

Only ``get_accessible_catalog_ids_for_zones`` reads the store. The other two are
restated verbatim so every name here is a port and the strict-mode check holds;
``resolve_table_filter`` returns a SQLAlchemy predicate, as upstream.
"""
from __future__ import annotations

from typing import Any

from auto_ontology.dal_rdf._common import P, client, values, wrap
from auto_ontology.rdf.vocab import row_id, row_iri_single

PORTS = [
    "get_accessible_catalog_ids_for_zones",
    "resolve_accessible_catalog_ids",
    "resolve_table_filter",
]


def catalog_tree() -> tuple[dict[str, str], dict[str, str]]:
    """``(table_iri -> schema_iri, schema_iri -> database_iri)`` over the whole catalog.

    Shared with ``zones.py`` (this lane's helper; not a DAL name, so never installed).
    """
    table_schema = {
        r["t"]: r["s"]
        for r in client().select(
            "SELECT ?t ?s WHERE { " + wrap(f"?t {P.catalog_table.ref_schema_id} ?s") + " }"
        )
    }
    schema_db = {
        r["s"]: r["d"]
        for r in client().select(
            "SELECT ?s ?d WHERE { " + wrap(f"?s {P.catalog_schema.ref_database_id} ?d") + " }"
        )
    }
    return table_schema, schema_db


def get_accessible_catalog_ids_for_zones(zone_ids: list[str]) -> dict[str, set[str]]:
    """Catalog ids reachable through *zone_ids*, one level from each direct grant."""
    if not zone_ids:
        return {"db_ids": set(), "schema_ids": set(), "table_ids": set()}

    zones = [row_iri_single("zone", z) for z in zone_ids]
    # One row per direct grant: its zone_target and whichever of the three
    # nullable references it carries.
    grants = client().select(
        "SELECT DISTINCT ?db ?sc ?tb WHERE { "
        + wrap(
            f"{values('z', zones)} ?zt {P.zone_target.ref_zone_id} ?z . "
            f"OPTIONAL {{ ?zt {P.zone_target.ref_database_id} ?db }} "
            f"OPTIONAL {{ ?zt {P.zone_target.ref_schema_id} ?sc }} "
            f"OPTIONAL {{ ?zt {P.zone_target.ref_table_id} ?tb }}"
        )
        + " }"
    )
    g_db = {r["db"] for r in grants if r.get("db")}
    g_sc = {r["sc"] for r in grants if r.get("sc")}
    g_tb = {r["tb"] for r in grants if r.get("tb")}

    table_schema, schema_db = catalog_tree()

    tabs = set(g_tb)
    schemas = set(g_sc)
    dbs = set(g_db)
    for t, s in table_schema.items():
        d = schema_db.get(s)
        if s in g_sc or (d is not None and d in g_db):
            tabs.add(t)
    for s, d in schema_db.items():
        if d in g_db:
            schemas.add(s)
        if s in g_sc:
            dbs.add(d)
    for t in g_tb:
        s = table_schema.get(t)
        if s is not None:
            schemas.add(s)
            d = schema_db.get(s)
            if d is not None:
                dbs.add(d)
    return {
        "db_ids": {row_id(i) for i in dbs},
        "schema_ids": {row_id(i) for i in schemas},
        "table_ids": {row_id(i) for i in tabs},
    }


def resolve_accessible_catalog_ids(
    zone_ids: list[str] | None,
    data_ids_by_zone: dict[str, set[str]] | None = None,
) -> dict[str, set[str]] | None:
    if zone_ids is None:
        return None
    if data_ids_by_zone is not None:
        return data_ids_by_zone
    return get_accessible_catalog_ids_for_zones(zone_ids)


def resolve_table_filter(
    zone_ids: list[str] | None,
    column_ref: Any,
    *,
    data_ids_by_zone: dict[str, set[str]] | None = None,
) -> Any:
    if zone_ids is None:
        return None
    resolved = resolve_accessible_catalog_ids(zone_ids, data_ids_by_zone)
    return column_ref.in_(list(resolved["table_ids"]))
