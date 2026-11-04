# SPDX-License-Identifier: Apache-2.0
"""RDF ports of ``auto_ontology.dal.zones`` reads."""
from __future__ import annotations

from typing import Any

from auto_ontology.dal_rdf._common import P, client, values, wrap
from auto_ontology.dal_rdf.users import catalog_tree
from auto_ontology.rdf.vocab import row_id, row_iri_single
from auto_ontology.server.zones.utils import format_zone

PORTS = ["list_zones", "get_zone_by_id", "fetch_table_zones_map"]

_ZONE_COLS = ("name", "description", "color", "enabled")


def _zone_select(extra: str = "") -> list[dict[str, Any]]:
    opt = " ".join(f"OPTIONAL {{ ?z {getattr(P.zone, c)} ?{c} }}" for c in _ZONE_COLS)
    rows = client().select(
        f"SELECT ?z {' '.join('?' + c for c in _ZONE_COLS)} WHERE {{ "
        + wrap(f"?z a <urn:ao:zone> . {opt} {extra}")
        + " }"
    )
    for r in rows:
        r["id"] = row_id(r.pop("z"))
        for c in _ZONE_COLS:
            r.setdefault(c, None)
    return rows


def _zone_row(row: dict[str, Any]) -> dict[str, Any]:
    return {k: row[k] for k in ("id", "name", "description", "color", "enabled")}


def list_zones() -> list[dict[str, Any]]:
    rows = _zone_select()
    rows.sort(key=lambda r: (r["name"] or "", r["id"]))
    return [format_zone(_zone_row(r)) for r in rows]


def _zone_items(zone_iri: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for tier, table in (("database", "catalog_database"), ("schema", "catalog_schema"), ("table", "catalog_table")):
        ref = getattr(P.zone_target, f"ref_{tier}_id")
        rows = client().select(
            "SELECT ?x ?name WHERE { "
            + wrap(
                f"?zt {P.zone_target.ref_zone_id} {'<' + zone_iri + '>'} ; {ref} ?x . "
                f"?x {getattr(getattr(P, table), 'name')} ?name"
            )
            + " }"
        )
        items.extend({"id": row_id(r["x"]), "name": r["name"], "label": tier} for r in rows)
    return items


def get_zone_by_id(zone_id: str) -> dict[str, Any] | None:
    zone_iri = row_iri_single("zone", zone_id)
    rows = _zone_select(values("z", [zone_iri]))
    if not rows:
        return None
    return format_zone(_zone_row(rows[0]), items=_zone_items(zone_iri))


def fetch_table_zones_map(
    zone_ids: list[str] | None = None,
    data_ids_by_zone: dict[str, set[str]] | None = None,
    table_ids: list[str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    from auto_ontology.dal import users

    resolved = users.resolve_accessible_catalog_ids(zone_ids, data_ids_by_zone)

    filter_ids: set[str] | None = None
    if resolved is not None:
        filter_ids = set(resolved["table_ids"])
    if table_ids is not None:
        filter_ids = set(table_ids) if filter_ids is None else filter_ids & set(table_ids)
    if filter_ids is not None and not filter_ids:
        return {}

    opt = " ".join(
        f"OPTIONAL {{ ?zt {getattr(P.zone_target, f'ref_{t}_id')} ?{v} }}"
        for t, v in (("database", "db"), ("schema", "sc"), ("table", "tb"))
    )
    grants = client().select(
        "SELECT DISTINCT ?z ?name ?color ?enabled ?db ?sc ?tb WHERE { "
        + wrap(
            f"?zt {P.zone_target.ref_zone_id} ?z . {opt} "
            f"?z {P.zone.name} ?name . OPTIONAL {{ ?z {P.zone.color} ?color }} "
            f"OPTIONAL {{ ?z {P.zone.enabled} ?enabled }}"
        )
        + " }"
    )
    if zone_ids is not None:
        wanted = {row_iri_single("zone", z) for z in zone_ids}
        grants = [g for g in grants if g["z"] in wanted and g.get("enabled") is True]

    table_schema, schema_db = catalog_tree()
    by_db: dict[str, list[dict]] = {}
    by_sc: dict[str, list[dict]] = {}
    by_tb: dict[str, list[dict]] = {}
    for g in grants:
        zone = {"id": row_id(g["z"]), "name": g["name"], "color": g.get("color"), "enabled": g.get("enabled")}
        if g.get("tb"):
            by_tb.setdefault(g["tb"], []).append(zone)
        if g.get("sc"):
            by_sc.setdefault(g["sc"], []).append(zone)
        if g.get("db"):
            by_db.setdefault(g["db"], []).append(zone)

    result: dict[str, list[dict[str, Any]]] = {}
    for t_iri in sorted(table_schema, key=row_id):
        tid = row_id(t_iri)
        if filter_ids is not None and tid not in filter_ids:
            continue
        s_iri = table_schema[t_iri]
        covering = by_tb.get(t_iri, []) + by_sc.get(s_iri, []) + by_db.get(schema_db.get(s_iri), [])
        seen: set[tuple] = set()
        zones: list[dict] = []
        for z in sorted(covering, key=lambda z: (z["name"], z["id"])):
            key = (z["id"],)
            if key not in seen:
                seen.add(key)
                zones.append(z)
        if zones:
            result[tid] = zones
    return result
