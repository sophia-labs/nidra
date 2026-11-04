# SPDX-License-Identifier: Apache-2.0
"""Extra captures for the datasources_b lane (column-side and lookup reads)."""
from __future__ import annotations


def capture(cap, ids) -> None:
    from auto_ontology.dal import datasources as d

    run = cap.run
    film = ids["table_film"]
    rental = ids["table_rental"]
    col = ids["col_film_title"]

    # fetch_columns_for_table: paging and edges the base golden lacks.
    run("datasources.fetch_columns_for_table__page", lambda: d.fetch_columns_for_table(film, skip=1, limit=2))
    run("datasources.fetch_columns_for_table__past_end", lambda: d.fetch_columns_for_table(film, skip=500, limit=5))
    run("datasources.fetch_columns_for_table__limit0", lambda: d.fetch_columns_for_table(film, limit=0))
    run("datasources.fetch_columns_for_table__rental", lambda: d.fetch_columns_for_table(rental))
    run("datasources.fetch_columns_for_table__track", lambda: d.fetch_columns_for_table(ids["table_track"]))
    run("datasources.count_columns_for_table__unknown", lambda: d.count_columns_for_table("no-such-id"))
    run("datasources.count_columns_for_table__rental", lambda: d.count_columns_for_table(rental))
    run("datasources.fetch_parent_table_id_for_column__unknown", lambda: d.fetch_parent_table_id_for_column("no-such-id"))
    run("datasources.fetch_join_neighbors__unknown", lambda: d.fetch_join_neighbors("no-such-id"))
    run("datasources.fetch_join_neighbors__rental", lambda: d.fetch_join_neighbors(rental))
    run("datasources.fetch_join_neighbors__track", lambda: d.fetch_join_neighbors(ids["table_track"]))

    # find_column_id_by_table_and_name
    f = d.find_column_id_by_table_and_name
    run("datasources.find_column_id_by_table_and_name", lambda: f("film", "title"))
    run("datasources.find_column_id_by_table_and_name__case", lambda: f("FILM", "Title"))
    run("datasources.find_column_id_by_table_and_name__db", lambda: f("film", "title", "pagila"))
    run("datasources.find_column_id_by_table_and_name__wrong_db", lambda: f("film", "title", "chinook"))
    run("datasources.find_column_id_by_table_and_name__track", lambda: f("track", "name"))
    run("datasources.find_column_id_by_table_and_name__empty_table", lambda: f("", "title"))
    run("datasources.find_column_id_by_table_and_name__empty_column", lambda: f("film", ""))
    run("datasources.find_column_id_by_table_and_name__no_table", lambda: f("no_such_table", "title"))
    run("datasources.find_column_id_by_table_and_name__no_column", lambda: f("film", "no_such_col"))

    # find_table_key_columns
    k = d.find_table_key_columns
    run("datasources.find_table_key_columns", lambda: k("film"))
    run("datasources.find_table_key_columns__db", lambda: k("FILM", "pagila"))
    run("datasources.find_table_key_columns__rental", lambda: k("rental", "pagila"))
    run("datasources.find_table_key_columns__track", lambda: k("track", "chinook"))
    run("datasources.find_table_key_columns__wrong_db", lambda: k("film", "chinook"))
    run("datasources.find_table_key_columns__empty", lambda: k(""))
    run("datasources.find_table_key_columns__unknown", lambda: k("no_such_table"))

    # fetch_item_by_id: each label, list labels, unknown label, not found.
    g = d.fetch_item_by_id
    run("datasources.fetch_item_by_id__database", lambda: g(ids["db_pagila"], "Database"))
    run("datasources.fetch_item_by_id__schema", lambda: g(ids["schema_public"], "Schema"))
    run("datasources.fetch_item_by_id__column", lambda: g(col, "Column"))
    run("datasources.fetch_item_by_id__table_pk", lambda: g(rental, "Table"))
    run("datasources.fetch_item_by_id__list", lambda: g(col, ["Table", "Column"]))
    run("datasources.fetch_item_by_id__list_table_first", lambda: g(film, ["Table", "Column"]))
    run("datasources.fetch_item_by_id__wrong_label", lambda: g(film, "Column"))
    run("datasources.fetch_item_by_id__unknown_label", lambda: g(film, "Term"))
    run("datasources.fetch_item_by_id__mixed_labels", lambda: g(film, ["Table", "Bogus"]))
    run("datasources.fetch_item_by_id__not_found", lambda: g("no-such-id", "Table"))
