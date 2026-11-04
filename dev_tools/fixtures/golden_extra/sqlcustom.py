# SPDX-License-Identifier: Apache-2.0
"""Captures for the sql_attributes / custom_analyses / pql_analyses / candidates
porting lane: the reads the main golden does not cover, and the parameter
variants (zone modes, paging, empty input, not-found, every label)."""
from __future__ import annotations

from typing import Any


def capture(cap, ids: dict[str, Any]) -> None:
    from dev_tools.fixtures.capture_dal_golden import _all_statements, _zone_modes
    from auto_ontology.dal import candidates, custom_analyses, pql_analyses, sql_attributes

    run = cap.run
    sqlattr = ids["sqlattr_revenue"]
    analysis = ids["analysis_top_films"]
    pql = ids["pql_churn"]
    film_term = ids["term_Film"]
    modes = _zone_modes(ids)

    # -- sql_attributes ----------------------------------------------------
    for label, zone_ids in modes:
        run(
            f"sql_attributes.fetch_sql_attributes_by_term_id__zone_{label}",
            lambda z=zone_ids: sql_attributes.fetch_sql_attributes_by_term_id(film_term, z),
        )
        run(
            f"sql_attributes.get_full_sql_attribute_by_id__zone_{label}",
            lambda z=zone_ids: sql_attributes.get_full_sql_attribute_by_id(sqlattr, z),
        )
    run(
        "sql_attributes.fetch_sql_attributes_by_term_id__skip",
        lambda: sql_attributes.fetch_sql_attributes_by_term_id(film_term, skip=1),
    )
    run(
        "sql_attributes.fetch_sql_attributes_by_term_id__limit",
        lambda: sql_attributes.fetch_sql_attributes_by_term_id(film_term, limit=1),
    )
    run(
        "sql_attributes.fetch_sql_attributes_by_term_id__skip_past_end",
        lambda: sql_attributes.fetch_sql_attributes_by_term_id(film_term, skip=100),
    )
    for name in ("Customer", "Payment", "Track"):
        run(
            f"sql_attributes.fetch_sql_attributes_by_term_id__term_{name}",
            lambda n=name: sql_attributes.fetch_sql_attributes_by_term_id(ids[f"term_{n}"]),
        )
    run(
        "sql_attributes.fetch_sql_attributes_by_term_id__unknown",
        lambda: sql_attributes.fetch_sql_attributes_by_term_id("no-such-term"),
    )
    for label, zone_ids in modes:
        run(
            f"sql_attributes.get_full_sql_attribute_by_id__all_zone_{label}",
            lambda z=zone_ids: [
                sql_attributes.get_full_sql_attribute_by_id(a["id"], z)
                for a in sql_attributes.list_sql_attributes()
            ],
        )
    run(
        "sql_attributes.get_full_sql_attribute_by_id__unknown",
        lambda: sql_attributes.get_full_sql_attribute_by_id("no-such-id"),
    )
    run(
        "sql_attributes.fetch_sql_attributes_with_sql__empty",
        lambda: sql_attributes.fetch_sql_attributes_with_sql([]),
    )
    run(
        "sql_attributes.fetch_sql_attributes_with_sql__unknown",
        lambda: sql_attributes.fetch_sql_attributes_with_sql(["no-such-id"]),
    )
    run(
        "sql_attributes.fetch_sql_attributes_with_sql__all",
        lambda: sql_attributes.fetch_sql_attributes_with_sql(
            [a["id"] for a in sql_attributes.list_sql_attributes()]
        ),
    )
    run(
        "sql_attributes.fetch_tables_from_sql_attributes__empty",
        lambda: sql_attributes.fetch_tables_from_sql_attributes([]),
    )
    run(
        "sql_attributes.fetch_tables_from_sql_attributes__unknown",
        lambda: sql_attributes.fetch_tables_from_sql_attributes(["no-such-id"]),
    )
    run(
        "sql_attributes.fetch_tables_from_sql_attributes__all",
        lambda: sql_attributes.fetch_tables_from_sql_attributes(
            [a["id"] for a in sql_attributes.list_sql_attributes()]
        ),
    )
    run(
        "sql_attributes.find_attr_by_name__exclude_self",
        lambda: sql_attributes.find_attr_by_name("total revenue", sqlattr),
    )
    run(
        "sql_attributes.find_attr_by_name__exclude_other",
        lambda: sql_attributes.find_attr_by_name("total revenue", "no-such-id"),
    )
    run(
        "sql_attributes.find_attr_by_name__empty_name",
        lambda: sql_attributes.find_attr_by_name("", None),
    )
    run(
        "sql_attributes.find_attr_by_name__case",
        lambda: sql_attributes.find_attr_by_name("TOTAL REVENUE", None),
    )

    # -- custom_analyses ---------------------------------------------------
    run(
        "custom_analyses.fetch_custom_analyses",
        lambda: custom_analyses.fetch_custom_analyses(),
    )
    run(
        "custom_analyses.fetch_custom_analyses_with_sql__empty",
        lambda: custom_analyses.fetch_custom_analyses_with_sql([]),
    )
    run(
        "custom_analyses.fetch_custom_analyses_with_sql__unknown",
        lambda: custom_analyses.fetch_custom_analyses_with_sql(["no-such-id"]),
    )
    run(
        "custom_analyses.fetch_custom_analyses_with_sql__all",
        lambda: custom_analyses.fetch_custom_analyses_with_sql(
            [a["id"] for a in custom_analyses.list_custom_analyses()]
        ),
    )
    run(
        "custom_analyses.fetch_tables_from_custom_analyses__empty",
        lambda: custom_analyses.fetch_tables_from_custom_analyses([]),
    )
    run(
        "custom_analyses.fetch_tables_from_custom_analyses__unknown",
        lambda: custom_analyses.fetch_tables_from_custom_analyses(["no-such-id"]),
    )
    run(
        "custom_analyses.fetch_tables_from_custom_analyses__all",
        lambda: custom_analyses.fetch_tables_from_custom_analyses(
            [a["id"] for a in custom_analyses.list_custom_analyses()]
        ),
    )
    run("custom_analyses.custom_analysis_exists", lambda: custom_analyses.custom_analysis_exists())
    run("custom_analyses.custom_analysis_exists__none", lambda: custom_analyses.custom_analysis_exists(None))
    run("custom_analyses.custom_analysis_exists__empty_name", lambda: custom_analyses.custom_analysis_exists(""))
    for db in ("pagila", "chinook", "no-such-database"):
        run(
            f"custom_analyses.custom_analysis_exists__{db}",
            lambda d=db: custom_analyses.custom_analysis_exists(d),
        )

    # -- pql_analyses ------------------------------------------------------
    run(
        "pql_analyses.fetch_pql_analyses_by_ids__empty",
        lambda: pql_analyses.fetch_pql_analyses_by_ids([]),
    )
    run(
        "pql_analyses.fetch_pql_analyses_by_ids__unknown",
        lambda: pql_analyses.fetch_pql_analyses_by_ids(["no-such-id"]),
    )
    run(
        "pql_analyses.fetch_pql_analyses_by_ids__all",
        lambda: pql_analyses.fetch_pql_analyses_by_ids(
            [a["id"] for a in pql_analyses.list_pql_analyses()]
        ),
    )
    run(
        "pql_analyses.fetch_pql_analyses_by_ids__mixed",
        lambda: pql_analyses.fetch_pql_analyses_by_ids([pql, "no-such-id"]),
    )

    # -- candidates.expand_info -------------------------------------------
    statements = _all_statements()
    sql_id = statements[0]["id"] if statements else "no-such-id"

    def pair(label, ident):
        return {"id": ident, "label": label}

    run("candidates.expand_info__none", lambda: candidates.expand_info(None))
    run("candidates.expand_info__empty", lambda: candidates.expand_info([]))
    run(
        "candidates.expand_info__malformed",
        lambda: candidates.expand_info(
            ["junk", {"label": "Table"}, {"id": None, "label": "Table"}, {"id": "x"}, {"id": "x", "label": "  "}]
        ),
    )
    run(
        "candidates.expand_info__unknown_label",
        lambda: candidates.expand_info([pair("Bogus", ids["table_film"])]),
    )
    run(
        "candidates.expand_info__not_found",
        lambda: candidates.expand_info(
            [pair(label, "no-such-id") for label in ("Database", "Schema", "Table", "Column", "Sql", "CustomAnalysis", "SqlAttribute")]
        ),
    )
    run("candidates.expand_info__Database", lambda: candidates.expand_info([pair("Database", ids["db_pagila"])]))
    run("candidates.expand_info__Schema", lambda: candidates.expand_info([pair("Schema", ids["schema_public"])]))
    run(
        "candidates.expand_info__Table",
        lambda: candidates.expand_info([pair("Table", ids["table_film"]), pair("Table", ids["table_rental"])]),
    )
    run("candidates.expand_info__Table_track", lambda: candidates.expand_info([pair("Table", ids["table_track"])]))
    run(
        "candidates.expand_info__Column",
        lambda: candidates.expand_info(
            [pair("Column", ids["col_film_film_id"]), pair("Column", ids["col_film_title"]), pair("Column", ids["col_rental_customer_id"])]
        ),
    )
    run("candidates.expand_info__Sql", lambda: candidates.expand_info([pair("Sql", sql_id)]))
    run("candidates.expand_info__CustomAnalysis", lambda: candidates.expand_info([pair("CustomAnalysis", analysis)]))
    run("candidates.expand_info__SqlAttribute", lambda: candidates.expand_info([pair("SqlAttribute", sqlattr)]))
    run(
        "candidates.expand_info__SqlAttribute_all",
        lambda: candidates.expand_info(
            [pair("SqlAttribute", a["id"]) for a in sql_attributes.list_sql_attributes()]
        ),
    )
    run(
        "candidates.expand_info__CustomAnalysis_all",
        lambda: candidates.expand_info(
            [pair("CustomAnalysis", a["id"]) for a in custom_analyses.list_custom_analyses()]
        ),
    )
    run(
        "candidates.expand_info__duplicates",
        lambda: candidates.expand_info([pair("Table", ids["table_film"]), pair("Table", ids["table_film"])]),
    )
    run(
        "candidates.expand_info__mixed",
        lambda: candidates.expand_info(
            [
                pair("Table", ids["table_payment"]),
                pair("Column", ids["col_customer_customer_id"]),
                pair("SqlAttribute", sqlattr),
                pair("CustomAnalysis", analysis),
                pair("Database", ids["db_pagila"]),
                pair("Bogus", "zzz"),
                pair("Table", "no-such-id"),
            ]
        ),
    )
