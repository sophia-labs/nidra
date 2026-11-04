# SPDX-License-Identifier: Apache-2.0
"""Captures for the datasources2 lane: the remaining golden-covered reads of
datasources / custom_analyses / pql_analyses / sql_attributes, with the empty,
not-found, paging, search, scoping and exclude paths the base golden lacks."""
from __future__ import annotations

from typing import Any


def capture(cap, ids: dict[str, Any]) -> None:
    from dev_tools.fixtures.capture_dal_golden import _zone_modes
    from auto_ontology.dal import custom_analyses as ca
    from auto_ontology.dal import datasources as d
    from auto_ontology.dal import pql_analyses as pq
    from auto_ontology.dal import sql_attributes as sa

    run = cap.run
    film, rental, track = ids["table_film"], ids["table_rental"], ids["table_track"]
    col_title, col_cust = ids["col_film_title"], ids["col_rental_customer_id"]
    analysis, pql, sqlattr = ids["analysis_top_films"], ids["pql_churn"], ids["sqlattr_revenue"]
    modes = _zone_modes(ids)

    # -- datasources -------------------------------------------------------
    run("datasources.fetch_all_tables_without_term__d2_pagila", lambda: d.fetch_all_tables_without_term("pagila"))
    run("datasources.fetch_all_tables_without_term__d2_chinook", lambda: d.fetch_all_tables_without_term("chinook"))
    run("datasources.fetch_all_tables_without_term__d2_unknown", lambda: d.fetch_all_tables_without_term("no-such-db"))
    run("datasources.fetch_schema_ids_for_database__d2_chinook", lambda: d.fetch_schema_ids_for_database("chinook"))
    run("datasources.fetch_schema_ids_for_database__d2_unknown", lambda: d.fetch_schema_ids_for_database("no-such-db"))
    run("datasources.fetch_bridge_table_candidates__d2_chinook", lambda: d.fetch_bridge_table_candidates("chinook"))
    run("datasources.fetch_bridge_table_candidates__d2_unknown", lambda: d.fetch_bridge_table_candidates("no-such-db"))
    run("datasources.fetch_col_table_contexts__d2_empty", lambda: d.fetch_col_table_contexts([]))
    run("datasources.fetch_col_table_contexts__d2_unknown", lambda: d.fetch_col_table_contexts(["no-such-id"]))
    run("datasources.fetch_col_table_contexts__d2_mixed", lambda: d.fetch_col_table_contexts([col_title, "no-such-id"]))
    run("datasources.fetch_tables_and_columns_by_node_ids__d2_empty", lambda: d.fetch_tables_and_columns_by_node_ids([]))
    run("datasources.fetch_tables_and_columns_by_node_ids__d2_unknown", lambda: d.fetch_tables_and_columns_by_node_ids(["no-such-id"]))
    run("datasources.fetch_tables_and_columns_by_node_ids__d2_table", lambda: d.fetch_tables_and_columns_by_node_ids([rental]))
    run("datasources.fetch_tables_and_columns_by_node_ids__d2_column", lambda: d.fetch_tables_and_columns_by_node_ids([col_cust]))
    run("datasources.fetch_tables_and_columns_by_node_ids__d2_overlap", lambda: d.fetch_tables_and_columns_by_node_ids([film, col_title, col_cust]))
    run("datasources.fetch_tables_and_columns_by_node_ids__d2_track", lambda: d.fetch_tables_and_columns_by_node_ids([track]))

    # -- custom_analyses ---------------------------------------------------
    for label, z in modes:
        run(f"custom_analyses.count_custom_analyses__d2_zone_{label}", lambda z=z: ca.count_custom_analyses(z))
        run(f"custom_analyses.list_custom_analyses__d2_zone_{label}_search", lambda z=z: ca.list_custom_analyses(z, search="film"))
    run("custom_analyses.list_custom_analyses__d2_search_upper", lambda: ca.list_custom_analyses(search="  TOP  "))
    run("custom_analyses.list_custom_analyses__d2_search_none", lambda: ca.list_custom_analyses(search="zzz-nothing"))
    run("custom_analyses.list_custom_analyses__d2_wildcard", lambda: ca.list_custom_analyses(search="%"))
    run("custom_analyses.list_custom_analyses__d2_underscore", lambda: ca.list_custom_analyses(search="_"))
    run("custom_analyses.list_custom_analyses__d2_blank_search", lambda: ca.list_custom_analyses(search="   "))
    run("custom_analyses.list_custom_analyses__d2_skip", lambda: ca.list_custom_analyses(skip=1))
    run("custom_analyses.list_custom_analyses__d2_limit", lambda: ca.list_custom_analyses(limit=1))
    run("custom_analyses.list_custom_analyses__d2_limit0", lambda: ca.list_custom_analyses(limit=0))
    run("custom_analyses.list_custom_analyses__d2_past_end", lambda: ca.list_custom_analyses(skip=500, limit=5))
    run("custom_analyses.count_custom_analyses__d2_search", lambda: ca.count_custom_analyses(search="top"))
    run("custom_analyses.count_custom_analyses__d2_search_none", lambda: ca.count_custom_analyses(search="zzz-nothing"))
    run("custom_analyses.find_analysis_by_name__d2_exclude_self", lambda: ca.find_analysis_by_name("Top rented films", analysis))
    run("custom_analyses.find_analysis_by_name__d2_exclude_other", lambda: ca.find_analysis_by_name("Top rented films", "no-such-id"))
    run("custom_analyses.find_analysis_by_name__d2_absent", lambda: ca.find_analysis_by_name("no such analysis", None))
    run("custom_analyses.find_analysis_by_name__d2_case", lambda: ca.find_analysis_by_name("top rented films", None))
    run("custom_analyses.find_analysis_by_name__d2_empty", lambda: ca.find_analysis_by_name("", None))
    sql_text = next((r["sql"] for r in ca.list_custom_analyses() if r["id"] == analysis), "")
    run("custom_analyses.find_analysis_by_sql__d2_exact", lambda: ca.find_analysis_by_sql(sql_text, None))
    run("custom_analyses.find_analysis_by_sql__d2_exclude_self", lambda: ca.find_analysis_by_sql(sql_text, analysis))
    run("custom_analyses.find_analysis_by_sql__d2_whitespace", lambda: ca.find_analysis_by_sql(sql_text + " ", None))
    run("custom_analyses.find_analysis_by_sql__d2_absent", lambda: ca.find_analysis_by_sql("select 1", None))
    run("custom_analyses.get_custom_analysis_by_id__d2_unknown", lambda: ca.get_custom_analysis_by_id("no-such-id"))
    run("custom_analyses.fetch_database_name_for_analysis", lambda: ca.fetch_database_name_for_analysis(analysis))
    run("custom_analyses.fetch_database_name_for_analysis__d2_unknown", lambda: ca.fetch_database_name_for_analysis("no-such-id"))
    for i, row in enumerate(ca.list_custom_analyses()):
        run(f"custom_analyses.fetch_database_name_for_analysis__d2_each{i}", lambda a=row["id"]: ca.fetch_database_name_for_analysis(a))

    # -- pql_analyses ------------------------------------------------------
    pql_row = next((r for r in pq.list_pql_analyses() if r["id"] == pql), {})
    run("pql_analyses.list_pql_analyses__d2_search", lambda: pq.list_pql_analyses(search="CHURN"))
    run("pql_analyses.list_pql_analyses__d2_search_none", lambda: pq.list_pql_analyses(search="zzz-nothing"))
    run("pql_analyses.list_pql_analyses__d2_wildcard", lambda: pq.list_pql_analyses(search="%"))
    run("pql_analyses.list_pql_analyses__d2_skip", lambda: pq.list_pql_analyses(skip=1))
    run("pql_analyses.list_pql_analyses__d2_limit", lambda: pq.list_pql_analyses(limit=1))
    run("pql_analyses.list_pql_analyses__d2_past_end", lambda: pq.list_pql_analyses(skip=500, limit=5))
    run("pql_analyses.count_pql_analyses", lambda: pq.count_pql_analyses())
    run("pql_analyses.count_pql_analyses__d2_search", lambda: pq.count_pql_analyses(search="churn"))
    run("pql_analyses.count_pql_analyses__d2_search_none", lambda: pq.count_pql_analyses(search="zzz-nothing"))
    run("pql_analyses.find_pql_analysis_by_name__d2_exclude_self", lambda: pq.find_pql_analysis_by_name("Customer churn", pql))
    run("pql_analyses.find_pql_analysis_by_name__d2_exclude_other", lambda: pq.find_pql_analysis_by_name("Customer churn", "no-such-id"))
    run("pql_analyses.find_pql_analysis_by_name__d2_absent", lambda: pq.find_pql_analysis_by_name("no such", None))
    run("pql_analyses.find_pql_analysis_by_pql", lambda: pq.find_pql_analysis_by_pql(pql_row.get("pql", ""), None))
    run("pql_analyses.find_pql_analysis_by_pql__d2_exclude_self", lambda: pq.find_pql_analysis_by_pql(pql_row.get("pql", ""), pql))
    run("pql_analyses.find_pql_analysis_by_pql__d2_absent", lambda: pq.find_pql_analysis_by_pql("PREDICT nothing", None))
    run("pql_analyses.get_pql_analysis_by_id__d2_unknown", lambda: pq.get_pql_analysis_by_id("no-such-id"))

    # -- sql_attributes ----------------------------------------------------
    term_ids = [ids["term_Film"], ids["term_Customer"], ids["term_Payment"], ids["term_Track"]]
    for label, z in modes:
        run(f"sql_attributes.count_sql_attributes_by_term_id__d2_zone_{label}", lambda z=z: sa.count_sql_attributes_by_term_id(ids["term_Film"], z))
        run(f"sql_attributes.fetch_sql_attribute_counts__d2_{label}_terms", lambda z=z: sa.fetch_sql_attribute_counts(z, None, term_ids[:2]))
        run(f"sql_attributes.fetch_sql_attribute_counts__d2_{label}_empty_terms", lambda z=z: sa.fetch_sql_attribute_counts(z, None, []))
    run("sql_attributes.fetch_sql_attribute_counts__d2_terms_only", lambda: sa.fetch_sql_attribute_counts(None, None, [ids["term_Film"]]))
    run("sql_attributes.fetch_sql_attribute_counts__d2_unknown_term", lambda: sa.fetch_sql_attribute_counts(None, None, ["no-such-id"]))
    run("sql_attributes.fetch_sql_attribute_counts__d2_data_ids", lambda: sa.fetch_sql_attribute_counts([ids["zone_film"]], {"table_ids": {film, rental}}, None))
    for name in ("Customer", "Payment", "Track"):
        run(f"sql_attributes.count_sql_attributes_by_term_id__d2_{name}", lambda n=name: sa.count_sql_attributes_by_term_id(ids[f"term_{n}"]))
    run("sql_attributes.count_sql_attributes_by_term_id__d2_unknown", lambda: sa.count_sql_attributes_by_term_id("no-such-id"))
    run("sql_attributes.fetch_sql_attribute_docs__d2_unknown", lambda: sa.fetch_sql_attribute_docs("no-such-id"))
    run("sql_attributes.get_sql_attribute_by_id__d2_unknown", lambda: sa.get_sql_attribute_by_id("no-such-id"))
    attrs = sa.list_sql_attributes()
    for i, row in enumerate(attrs):
        run(f"sql_attributes.fetch_sql_attribute_docs__d2_each{i}", lambda a=row["id"]: sa.fetch_sql_attribute_docs(a))
        run(f"sql_attributes.get_sql_attribute_by_id__d2_each{i}", lambda a=row["id"]: sa.get_sql_attribute_by_id(a))
    me = next((r for r in attrs if r["id"] == sqlattr), None)
    expr = (me or {}).get("expression") or ""
    t = ids["term_Film"]
    run("sql_attributes.find_attr_by_expression", lambda: sa.find_attr_by_expression(term_id=t, expression=expr, exclude_id=None))
    run("sql_attributes.find_attr_by_expression__d2_exclude_self", lambda: sa.find_attr_by_expression(term_id=t, expression=expr, exclude_id=sqlattr))
    run("sql_attributes.find_attr_by_expression__d2_case_ws", lambda: sa.find_attr_by_expression(term_id=t, expression="  " + expr.upper().replace(" ", " \n\t ") + " ", exclude_id=None))
    run("sql_attributes.find_attr_by_expression__d2_other_term", lambda: sa.find_attr_by_expression(term_id=ids["term_Customer"], expression=expr, exclude_id=None))
    run("sql_attributes.find_attr_by_expression__d2_absent", lambda: sa.find_attr_by_expression(term_id=t, expression="no such expression", exclude_id=None))
    run("sql_attributes.find_attr_by_expression__d2_empty", lambda: sa.find_attr_by_expression(term_id=t, expression="", exclude_id=None))
    run("sql_attributes.find_attr_by_expression__d2_unknown_term", lambda: sa.find_attr_by_expression(term_id="no-such-id", expression=expr, exclude_id=None))
