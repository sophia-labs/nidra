# SPDX-License-Identifier: Apache-2.0
"""RDF port of ``dal.attributes.fetch_semantic_fk_join_groups`` (gaps lane)."""
from __future__ import annotations

from typing import Any

from auto_ontology.dal_rdf._common import P, client, lit, wrap
from auto_ontology.rdf.vocab import row_id

PORTS = ["fetch_semantic_fk_join_groups"]


def _o(var: str, subject: str, prop: str) -> str:
    return f"OPTIONAL {{ {subject} {prop} {var} }} "


def fetch_semantic_fk_join_groups(database_name: str) -> list[dict[str, Any]]:
    """Semantic FKs with the context needed to choose one join default."""
    sparql = (
        "SELECT ?stb ?stn ?std ?ssn ?sc ?scn ?scd ?attr ?an ?ad ?ttb ?ttn ?ttd ?tsn ?tc ?tcn ?tcd ?use WHERE { "
        + wrap(
            f"?db {P.catalog_database.name} {lit(database_name)} . "
            f"?ss {P.catalog_schema.ref_database_id} ?db ; {P.catalog_schema.name} ?ssn . "
            f"?stb {P.catalog_table.ref_schema_id} ?ss ; {P.catalog_table.name} ?stn . "
            f"?sc {P.catalog_column.ref_table_id} ?stb ; {P.catalog_column.name} ?scn . "
            f"?fk {P.column__semantic_fk.ref_column_id} ?sc ; {P.column__semantic_fk.ref_attribute_id} ?attr . "
            f"?attr {P.column_attribute.name} ?an . "
            f"?ha {P.column__has_attribute.ref_attribute_id} ?attr ; {P.column__has_attribute.ref_column_id} ?tc . "
            f"?tc {P.catalog_column.ref_table_id} ?ttb ; {P.catalog_column.name} ?tcn . "
            f"?ttb {P.catalog_table.ref_schema_id} ?ts ; {P.catalog_table.name} ?ttn . "
            f"?ts {P.catalog_schema.name} ?tsn . "
            + _o("?std", "?stb", P.catalog_table.description)
            + _o("?scd", "?sc", P.catalog_column.description)
            + _o("?ad", "?attr", P.column_attribute.description)
            + _o("?ttd", "?ttb", P.catalog_table.description)
            + _o("?tcd", "?tc", P.catalog_column.description)
            + _o("?use", "?fk", P.column__semantic_fk.use_in_join_paths)
        )
        + " }"
    )
    out = []
    for r in client().select(sparql):
        out.append(
            {
                "source_table_id": row_id(r["stb"]),
                "source_table_name": r["stn"],
                "source_table_description": r.get("std"),
                "source_schema_name": r["ssn"],
                "source_column_id": row_id(r["sc"]),
                "source_column_name": r["scn"],
                "source_column_description": r.get("scd"),
                "target_attribute_id": row_id(r["attr"]),
                "target_attribute_name": r["an"],
                "target_attribute_description": r.get("ad"),
                "target_table_id": row_id(r["ttb"]),
                "target_table_name": r["ttn"],
                "target_table_description": r.get("ttd"),
                "target_schema_name": r["tsn"],
                "target_column_id": row_id(r["tc"]),
                "target_column_name": r["tcn"],
                "target_column_description": r.get("tcd"),
                "use_in_join_paths": r.get("use") if r.get("use") is not None else True,
            }
        )
    out.sort(key=lambda x: (x["source_table_id"], x["target_attribute_id"], x["source_column_name"].lower(), x["source_column_id"]))
    return out
