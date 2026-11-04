# SPDX-License-Identifier: Apache-2.0
"""RDF port of the ``auto_ontology.dal.zones`` writes.

``list_zones``, ``get_zone_by_id`` and ``fetch_table_zones_map`` are another
lane's ports (``zones.py``); this module has no other public read. The writes
that answer with a zone (``update_zone``, ``set_zone_enabled``) read it back
through ``auto_ontology.dal.zones.get_zone_by_id`` at call time, so whichever
backend is installed for it is the one used.
"""
from __future__ import annotations

from typing import Any

from auto_ontology.dal_rdf import _gov
from auto_ontology.dal_rdf._common import P, client, iri, select_rows, values, wrap
from auto_ontology.rdf import writes
from auto_ontology.rdf.vocab import row_id, row_iri_single
from auto_ontology.server.zones.utils import format_zone

PORTS = ["create_zone", "update_zone", "delete_zone", "set_zone_enabled"]

_TIERS = (
    ("database", "catalog_database", "database_id"),
    ("schema", "catalog_schema", "schema_id"),
    ("table", "catalog_table", "table_id"),
)


def _classify_items(item_ids: list[str]) -> dict[str, list[str]]:
    """Sort a flat list of ids into the tier each belongs to; unknown ids are dropped."""
    if not item_ids:
        return {"database": [], "schema": [], "table": []}
    ids = list(item_ids)
    return {tier: sorted(_gov.existing_ids(table, ids)) for tier, table, _ in _TIERS}


def _databases_for_items(item_ids: list[str]) -> list[str]:
    if not item_ids:
        return []
    items = [row_iri_single(t, i) for _, t, _ in _TIERS for i in item_ids]
    rows = client().select(
        "SELECT DISTINCT ?d WHERE { "
        + wrap(
            f"{values('item', items)} "
            f"{{ ?item a <urn:ao:catalog_database> . BIND(?item AS ?d) }} UNION "
            f"{{ ?item {P.catalog_schema.ref_database_id} ?d }} UNION "
            f"{{ ?item {P.catalog_table.ref_schema_id} ?s . ?s {P.catalog_schema.ref_database_id} ?d }}"
        )
        + " }"
    )
    return [row_id(r["d"]) for r in rows]


def _zones_touching(database_ids: list[str]) -> set[str]:
    dbs = [row_iri_single("catalog_database", d) for d in database_ids]
    rows = client().select(
        "SELECT DISTINCT ?z WHERE { "
        + wrap(
            f"{values('d', dbs)} ?zt {P.zone_target.ref_zone_id} ?z . "
            f"{{ ?zt {P.zone_target.ref_database_id} ?d }} UNION "
            f"{{ ?zt {P.zone_target.ref_schema_id} ?s . ?s {P.catalog_schema.ref_database_id} ?d }} UNION "
            f"{{ ?zt {P.zone_target.ref_table_id} ?t . ?t {P.catalog_table.ref_schema_id} ?s . "
            f"?s {P.catalog_schema.ref_database_id} ?d }}"
        )
        + " }"
    )
    return {row_id(r["z"]) for r in rows}


def _zone_name_exists(
    name: str, *, exclude_id: str | None = None, item_ids: list[str] | None = None
) -> bool:
    wanted = _gov.input_fold(name)
    matches = {
        z["id"]
        for z in select_rows("zone", ["name"])
        if _gov.stored_fold(z.get("name")) == wanted and z["id"] != exclude_id
    }
    database_ids = _databases_for_items(item_ids or [])
    if database_ids:
        matches &= _zones_touching(database_ids)
    return bool(matches)


def _current_item_ids(zone_id: str) -> list[str]:
    rows = _zone_targets(zone_id)
    return [r[c] for r in rows for c in ("database_id", "schema_id", "table_id") if r.get(c) is not None]


def _zone_targets(zone_id: str) -> list[dict[str, Any]]:
    return select_rows(
        "zone_target",
        ["database_id", "schema_id", "table_id"],
        extra=f"?s {P.zone_target.ref_zone_id} {iri(row_iri_single('zone', zone_id))} .",
    )


def _link_rows(zone_id: str, item_ids: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """The ``zone_target`` rows for *item_ids*, and the ids linked in the caller's order."""
    classified = _classify_items(item_ids)
    rows: list[dict[str, Any]] = []
    linked: set[str] = set()
    for tier, _, column in _TIERS:
        for item_id in classified[tier]:
            rows.append({"zone_id": zone_id, column: item_id})
            linked.add(item_id)
    ordered: list[str] = []
    seen: set[str] = set()
    for item_id in item_ids:
        if item_id in linked and item_id not in seen:
            seen.add(item_id)
            ordered.append(item_id)
    return rows, ordered


def create_zone(*, name: str, description: str | None, color: str, item_ids: list[str]) -> dict[str, Any]:
    if _zone_name_exists(name, item_ids=item_ids):
        raise ValueError(f"Zone with name {name!r} already exists")
    zone = writes.apply_defaults("zone", {"name": name, "description": description, "color": color})
    # The zone and its targets are one INSERT DATA.
    lines = writes.row_triples("zone", zone)
    ordered: list[str] = []
    if item_ids:
        targets, ordered = _link_rows(zone["id"], item_ids)
        for target in targets:
            lines.extend(writes.row_triples("zone_target", writes.apply_defaults("zone_target", target)))
    client().update(f"INSERT DATA {{ GRAPH {iri(writes.data_graph_iri())} {{ {' '.join(lines)} }} }}")
    result = format_zone(
        {c: zone.get(c) for c in ("id", "name", "description", "color", "enabled")}, items=[], enabled=True
    )
    if item_ids:
        result["items"] = ordered
    return result


def update_zone(
    *, zone_id: str, updates: dict[str, Any], item_ids: list[str] | None
) -> dict[str, Any] | None:
    from auto_ontology.dal import zones as dal_zones

    if not _gov.existing_ids("zone", [zone_id]):
        return None
    if "name" in updates and updates["name"] is not None:
        scope_ids = item_ids if item_ids is not None else _current_item_ids(zone_id)
        if _zone_name_exists(updates["name"], exclude_id=zone_id, item_ids=scope_ids):
            raise ValueError(f"Zone with name {updates['name']!r} already exists")
    fields = {k: v for k, v in updates.items() if k in {"name", "description", "color"}}
    if fields:
        writes.update_row("zone", zone_id, fields)
    if item_ids is not None:
        old = [r["id"] for r in _zone_targets(zone_id)]
        if old:
            writes.delete_rows("zone_target", old)
        rows, _ = _link_rows(zone_id, item_ids)
        if rows:
            writes.insert_rows("zone_target", rows)
    return dal_zones.get_zone_by_id(zone_id)


def delete_zone(zone_id: str) -> bool:
    if not _gov.existing_ids("zone", [zone_id]):
        return False
    writes.delete_rows("zone", [zone_id])  # zone_target goes by cascade
    return True


def set_zone_enabled(zone_id: str, enabled: bool) -> dict[str, Any] | None:
    from auto_ontology.dal import zones as dal_zones

    if not _gov.existing_ids("zone", [zone_id]):
        return None
    writes.update_row("zone", zone_id, {"enabled": enabled})
    return dal_zones.get_zone_by_id(zone_id)
