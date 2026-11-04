# SPDX-License-Identifier: Apache-2.0
"""Extra captures for lane datasources-a (reads with no upstream golden entry)."""
from __future__ import annotations


def capture(cap, ids) -> None:
    from auto_ontology.dal import datasources as ds

    run = cap.run
    film, rental = ids["table_film"], ids["table_rental"]
    chinook = next(d["id"] for d in ds.fetch_databases() if d["name"] == "chinook")
    zones = {
        "film": [ids["zone_film"]],
        "cross": [ids["zone_cross"]],
        "retired": [ids["zone_retired"]],
        "both": [ids["zone_film"], ids["zone_cross"]],
        "none": [],
    }

    # fetch_schemas_by_ids: empty/None mean every schema; unknown ids mean none.
    run("datasources.fetch_schemas_by_ids__one", lambda: ds.fetch_schemas_by_ids([ids["schema_public"]]))
    run("datasources.fetch_schemas_by_ids__empty", lambda: ds.fetch_schemas_by_ids([]))
    run("datasources.fetch_schemas_by_ids__none", lambda: ds.fetch_schemas_by_ids(None))
    run("datasources.fetch_schemas_by_ids__unknown", lambda: ds.fetch_schemas_by_ids(["no-such-id"]))
    run(
        "datasources.fetch_schemas_by_ids__mixed",
        lambda: ds.fetch_schemas_by_ids(["no-such-id", ids["schema_public"]]),
    )

    # find_table_id_by_name
    run("datasources.find_table_id_by_name", lambda: ds.find_table_id_by_name("film"))
    run("datasources.find_table_id_by_name__case", lambda: ds.find_table_id_by_name("FiLm"))
    run("datasources.find_table_id_by_name__db", lambda: ds.find_table_id_by_name("film", "pagila"))
    run("datasources.find_table_id_by_name__wrong_db", lambda: ds.find_table_id_by_name("film", "chinook"))
    run("datasources.find_table_id_by_name__ambiguous", lambda: ds.find_table_id_by_name("customer"))
    run(
        "datasources.find_table_id_by_name__ambiguous_scoped",
        lambda: ds.find_table_id_by_name("customer", "pagila"),
    )
    run("datasources.find_table_id_by_name__chinook", lambda: ds.find_table_id_by_name("Album", "chinook"))
    run("datasources.find_table_id_by_name__unknown", lambda: ds.find_table_id_by_name("no_such_table"))
    run("datasources.find_table_id_by_name__empty", lambda: ds.find_table_id_by_name(""))

    # fetch_tables_by_ids edges
    run("datasources.fetch_tables_by_ids__empty", lambda: ds.fetch_tables_by_ids([]))
    run("datasources.fetch_tables_by_ids__unknown", lambda: ds.fetch_tables_by_ids(["no-such-id"]))
    run("datasources.fetch_tables_by_ids__one", lambda: ds.fetch_tables_by_ids([film]))
    run(
        "datasources.fetch_tables_by_ids__mixed",
        lambda: ds.fetch_tables_by_ids(["no-such-id", ids["table_payment"], film]),
    )
    run("datasources.fetch_tables_by_ids__dup", lambda: ds.fetch_tables_by_ids([film, film]))

    # not-found paths
    run("datasources.fetch_table_by_name__unknown", lambda: ds.fetch_table_by_name("no_such_table"))
    run("datasources.fetch_table_by_name__ambiguous", lambda: ds.fetch_table_by_name("customer"))
    run("datasources.fetch_table_context__unknown", lambda: ds.fetch_table_context("no-such-id"))
    run("datasources.fetch_table_context__rental", lambda: ds.fetch_table_context(rental))
    run("datasources.fetch_table_context__category", lambda: ds.fetch_table_context(ids["table_category"]))
    run("datasources.fetch_schemas_for_database__unknown", lambda: ds.fetch_schemas_for_database("no-such-id"))
    run("datasources.fetch_schemas_for_database__chinook", lambda: ds.fetch_schemas_for_database(chinook))
    run("datasources.fetch_tables_for_schema__unknown", lambda: ds.fetch_tables_for_schema("no-such-id"))
    run(
        "datasources.fetch_tables_for_schema__ignored_db",
        lambda: ds.fetch_tables_for_schema(ids["schema_public"], database_name="chinook"),
    )

    # zone scoping on the reads that take it (the golden has only fetch_databases scoped)
    for label, zone_ids in zones.items():
        run(
            f"datasources.fetch_databases__zones_{label}",
            lambda z=zone_ids: ds.fetch_databases(zone_ids=z),
        )
        run(
            f"datasources.fetch_schemas_for_database__zones_{label}",
            lambda z=zone_ids: ds.fetch_schemas_for_database(ids["db_pagila"], zone_ids=z),
        )
        run(
            f"datasources.fetch_tables_for_schema__zones_{label}",
            lambda z=zone_ids: ds.fetch_tables_for_schema(ids["schema_public"], zone_ids=z),
        )
