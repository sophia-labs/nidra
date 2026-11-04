# SPDX-License-Identifier: Apache-2.0
"""Every write_diff case, under every write mode: the oracle of the write seam.

Each case is one ``write_diff`` run (a fresh Postgres copy and a fresh gardend
graph, the write applied on both, every DAL read diffed). This runs each case
under each ``NIDRA_WRITES`` mode given, ``--jobs`` at a time, and prints one line
per run plus a verdict table. The pass: every case has the same verdict in every
mode, and every case is SAME except the expected DIFFERENTs below.

    set -a; . ./.env; set +a
    export CONNECTION_STRINGS="postgresql://$POSTGRES_USER:$POSTGRES_PASSWORD@localhost:$POSTGRES_PORT/pagila,sqlite:///$PWD/dev_tools/fixtures/sql/chinook.sqlite"
    GARDEND_URL=... GARDEND_TOKEN=... uv run --no-sync python -m dev_tools.fixtures.write_diff_all \
        --modes sparql,commit --jobs 6 --out "$TMPDIR/wd-all"

``commit`` needs a gardend with the Emporium commit seam. Expected DIFFERENT:
``vacuity_check`` (the oracle's self-test: it writes on Postgres only) and
``semantic_fk_join_default`` with these arguments (a read-side discrepancy in
``terms.find_term_link_path``, the same on the branch before the seam, ``dc9ae378``).
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import subprocess
import sys
import time
from pathlib import Path

WC = "dev_tools.fixtures.write_cases."
SEM = WC + "writes_semantic:run"
FILM, ACTOR, FILM_ACTOR = "<table:pagila.public.film>", "<table:pagila.public.actor>", "<table:pagila.public.film_actor>"
PUB, ANA = "<schema:pagila.public>", "<schema:pagila.analytics>"
TITLE, RATING, FILM_ID = "<col:pagila.public.film.title>", "<col:pagila.public.film.rating>", "<col:pagila.public.film.film_id>"
ATTR_TITLE, ATTR_CUSTID, ATTR_FILMID = "<attr:Film/film title/title>", "<attr:Customer/customer id/customer_id>", "<attr:Film/film id/film_id>"

#: ``(target, kwargs[, extra env])``
CASES: list[tuple] = [
    # store (catalog store ports)
    (WC + "store:upsert_new_table", {"schema_id": PUB}),
    (WC + "store:upsert_existing_with_on_match", {"schema_id": PUB, "table_id": FILM}),
    (WC + "store:upsert_existing_by_natural_key", {"schema_id": PUB, "table_id": FILM}),
    (WC + "store:upsert_no_on_match", {"schema_id": PUB, "table_id": FILM}),
    (WC + "store:upsert_database_and_schema", {}),
    (WC + "store:resolve_ids", {"schema_id": PUB, "table_id": FILM}),
    (WC + "store:db_exists_and_update", {"db_id": "<db:pagila>"}),
    (WC + "store:delete_table_cascade", {"table_id": FILM_ACTOR}),
    (WC + "store:delete_schema_cascade", {"schema_id": ANA}),
    (WC + "store:delete_columns", {"column_ids": [RATING, "<col:pagila.public.film.length>"]}),
    (WC + "store:edge_batch", {"schema_id": PUB, "table_id": FILM}),
    (WC + "store:edge_batch_conflict", {"schema_id": PUB}),
    (WC + "store:update_properties_batch", {"table_id": FILM, "column_id": TITLE, "other_column_id": "<col:pagila.public.film.release_year>"}),
    (WC + "store:add_query_over_film", {"table_id": FILM, "column_id": TITLE, "other_column_id": FILM_ID}),
    (WC + "store:add_query_nested_rejected", {"table_id": FILM}),
    (WC + "store:counters", {"table_id": FILM}),
    (WC + "store:schemas_reads", {}),
    (WC + "store:add_schemas_edge", {"schema_id": PUB}),
    (WC + "store:merge_nodes_and_edges", {"schema_id": PUB, "table_id": FILM, "column_id": TITLE}),
    (WC + "store:fks_and_pks", {}),
    (WC + "store:reset_pks_only", {}),
    (WC + "store:transaction", {}),
    # data lane (datasources, attributes, pii, connections)
    (WC + "writes_data:sample_values_create", {"table_id": FILM}),
    (WC + "writes_data:sample_values_update", {"table_id": FILM}),
    (WC + "writes_data:sample_values_noop", {"table_id": FILM}),
    (WC + "writes_data:sample_values_empty", {"table_id": FILM}),
    (WC + "writes_data:uniqueness_create", {"table_id": FILM}),
    (WC + "writes_data:uniqueness_update", {"table_id": FILM}),
    (WC + "writes_data:date_formats_create", {"table_id": "<table:pagila.public.rental>"}),
    (WC + "writes_data:date_formats_update", {"table_id": "<table:pagila.public.rental>"}),
    (WC + "writes_data:junction_cases", {"table_id": FILM_ACTOR}),
    (WC + "writes_data:metadata_batch", {"database_name": "pagila"}),
    (WC + "writes_data:metadata_batch_keeps", {"database_name": "pagila"}),
    (WC + "writes_data:metadata_batch_empty", {"database_name": "pagila"}),
    (WC + "writes_data:patch_node_cases", {"table_id": FILM, "column_id": TITLE, "database_id": "<db:pagila>", "schema_id": PUB}),
    (WC + "writes_data:merge_attr_cases", {"table_id": FILM, "term_id": "<term:Film>"}),
    (WC + "writes_data:update_attr_cases", {"attr_id": ATTR_TITLE, "term_id": "<term:Film>", "other_term_id": "<term:Customer>"}),
    (WC + "writes_data:update_attr_conflict", {"attr_id": ATTR_TITLE, "term_id": "<term:Film>", "table_id": FILM}),
    (WC + "writes_data:semantic_fk_cases", {"new_col": "<col:pagila.public.store.manager_staff_id>", "new_attr": ATTR_CUSTID,
        "old_col": "<col:pagila.public.rental.customer_id>", "old_attr": ATTR_CUSTID, "foreign_col": "<col:chinook.main.Customer.CustomerId>"}),
    (WC + "writes_data:semantic_fk_join_default", {"table_id": "<table:pagila.public.rental>", "attr_id": ATTR_CUSTID,
        "selected": "<col:pagila.public.rental.customer_id>", "sibling": "<col:pagila.public.rental.staff_id>"}),
    (WC + "writes_data:pii_mark_cases", {"c1": TITLE, "c2": RATING, "c3": "<col:pagila.public.customer.email>"}),
    (WC + "writes_data:pii_tag_propagation", {"column_id": "<col:pagila.public.customer.email>"}),
    (WC + "writes_data:pii_realign_untag", {"column_id": TITLE, "sql_attr_id": "<sqlattr:film count>", "sql_attr_two": "<sqlattr:total revenue>", "attr_id": ATTR_TITLE}),
    (WC + "writes_data:connection_cases", {}),
    # governance lane (tags, rules, groups, zones)
    (WC + "writes_gov:tag_create", {}),
    (WC + "writes_gov:tag_conflict", {}),
    (WC + "writes_gov:tag_update", {}),
    (WC + "writes_gov:tag_update_noop", {}),
    (WC + "writes_gov:tag_delete", {"table_id": FILM, "column_id": TITLE, "term_id": "<term:Film>"}),
    (WC + "writes_gov:tag_attach_detach", {"table_id": FILM, "column_id": TITLE, "term_id": "<term:Film>", "attribute_id": ATTR_TITLE, "sql_attribute_id": "<sqlattr:film count>"}),
    (WC + "writes_gov:tag_rule_labels", {"table_a": FILM, "table_b": ACTOR, "column_id": TITLE, "term_id": "<term:Film>"}),
    (WC + "writes_gov:tag_rule_select", {"table_a": FILM, "table_b": ACTOR}),
    (WC + "writes_gov:tag_empty", {"table_a": FILM}),
    (WC + "writes_gov:tag_pii_rule_removal", {"column_id": "<col:pagila.public.customer.email>"}),
    (WC + "writes_gov:rule_create", {}),
    (WC + "writes_gov:rule_conflict", {}),
    (WC + "writes_gov:rule_update", {}),
    (WC + "writes_gov:rule_update_noop", {}),
    (WC + "writes_gov:rule_delete", {"table_a": FILM, "table_b": ACTOR, "column_id": TITLE}),
    (WC + "writes_gov:rule_delete_keep_tags", {"table_a": FILM, "table_b": ACTOR}),
    (WC + "writes_gov:rule_empty", {}),
    (WC + "writes_gov:group_create", {}),
    (WC + "writes_gov:group_conflict", {}),
    (WC + "writes_gov:group_update", {}),
    (WC + "writes_gov:group_update_noop", {}),
    (WC + "writes_gov:group_delete", {}),
    (WC + "writes_gov:group_empty", {}),
    (WC + "writes_gov:zone_create", {"table_id": FILM, "schema_id": PUB, "other_table_id": ACTOR}),
    (WC + "writes_gov:zone_conflict", {"table_id": FILM, "other_table_id": ACTOR}),
    (WC + "writes_gov:zone_update", {"zone_id": "<zone:Film domain>", "table_id": FILM, "other_table_id": ACTOR}),
    (WC + "writes_gov:zone_update_noop", {"zone_id": "<zone:Film domain>"}),
    (WC + "writes_gov:zone_update_conflict", {"zone_id": "<zone:Film domain>", "cross_zone_id": "<zone:Cross database>"}),
    (WC + "writes_gov:zone_delete", {"zone_id": "<zone:Film domain>"}),
    (WC + "writes_gov:zone_enabled", {"zone_id": "<zone:Film domain>", "retired_id": "<zone:Retired zone>"}),
    # model import
    (WC + "writes_import:roundtrip_replace", {}),
    (WC + "writes_import:roundtrip_merge", {}),
    (WC + "writes_import:merge_add_term", {"db_id": "<db:pagila>", "schema_id": PUB, "table_id": FILM, "film_id_col": FILM_ID, "title_col": TITLE}),
    (WC + "writes_import:replace_drop_term", {"term_name": "Rental"}),
    (WC + "writes_import:empty_document", {"replace": True}),
    (WC + "writes_import:unresolvable_foreign_key", {}),
    # gaps (owners, re-ingest diff, rule replay)
    (WC + "gaps:reingest_diff", {"drop_table": "category"}),
    (WC + "gaps:reingest_diff", {"drop_column": "film.rating", "retype_column": "film.length"}),
    (WC + "gaps:reingest_diff", {"add_table": "screening"}),
    (WC + "gaps:assign_owner_term", {"term_id": "<term:Film>"}),
    (WC + "gaps:assign_owner_term", {"term_id": "<term:Film>", "owner_id": None}),
    (WC + "gaps:assign_owner_unknown", {}),
    (WC + "gaps:rule_replay", {}),
    (WC + "gaps:rule_replay", {"drop_table_id": "<table:pagila.public.film_list>"}),
    (WC + "gaps:reingest_diff", {"retype_table": "film_list"}),
    # semantic write ports, with full table dumps
    (SEM, {"call": "auto_ontology.dal.terms:update_term", "kwargs": {"term_id": "<term:Film>", "name": "Movie", "description": "Edited", "name_certified": True, "touch": True, "modified_by": "u1"}, "tables": ["term"]}),
    (SEM, {"call": "auto_ontology.dal.terms:upsert_table_term", "kwargs": {"name": "Screening", "description": "shown", "table_id": FILM, "synonyms": ["showing", "projection"]}, "tables": ["term", "table__term"]}),
    (SEM, {"call": "auto_ontology.dal.terms:upsert_table_term", "kwargs": {"name": "Film", "description": "again", "table_id": FILM, "synonyms": ["flick"]}, "tables": ["term", "table__term"]}),
    (SEM, {"call": "auto_ontology.dal.sql_attributes:detach_existing_sql_edges", "kwargs": {"attr_id": "<sqlattr:film count>"}, "tables": ["sql_attribute__sql", "sql_query"]}),
    (SEM, {"call": "auto_ontology.dal.sql_attributes:link_to_term", "kwargs": {"attr_id": "<sqlattr:film count>", "term_id": "<term:Customer>"}, "tables": ["sql_attribute__term"]}),
    (SEM, {"call": "auto_ontology.dal.sql_attributes:link_to_term", "kwargs": {"attr_id": "<sqlattr:film count>", "term_id": "<term:Film>"}, "tables": ["sql_attribute__term"]}),
    (SEM, {"call": "auto_ontology.dal.sql_attributes:update_sql_attribute", "kwargs": {"attr_id": "<sqlattr:film count>", "name": "films", "description": "how many", "certified": True, "touch": True, "modified_by": "u1"}, "tables": ["sql_attribute"]}),
    (SEM, {"call": "auto_ontology.dal.sql_attributes:update_sql_attribute", "kwargs": {"attr_id": "<sqlattr:film count>", "name": "total revenue"}, "tables": ["sql_attribute"]}),
    (SEM, {"call": "auto_ontology.dal.sql_attributes:clear_sql_attribute_description_suggestions_for_term", "kwargs": {"term_id": "<term:Film>"}, "tables": ["sql_attribute"],
           "before": [["auto_ontology.dal.sql_attributes:set_sql_attribute_description_suggestion", {"attr_id": "<sqlattr:film count>", "description_suggestion": "s1"}],
                      ["auto_ontology.dal.sql_attributes:set_sql_attribute_description_suggestion", {"attr_id": "<sqlattr:films per category>", "description_suggestion": "s2"}]]}),
    (SEM, {"call": "auto_ontology.dal.sql_attributes:clear_sql_attribute_description_suggestion", "kwargs": {"attr_id": "<sqlattr:film count>"}, "tables": ["sql_attribute"],
           "before": [["auto_ontology.dal.sql_attributes:set_sql_attribute_description_suggestion", {"attr_id": "<sqlattr:film count>", "description_suggestion": "s1"}]]}),
    (SEM, {"call": "auto_ontology.dal.sql_attributes:delete_sql_attribute_node", "kwargs": {"attr_id": "<sqlattr:film count>"}, "tables": ["sql_attribute", "sql_attribute__term", "sql_attribute__sql"]}),
    (SEM, {"call": "auto_ontology.dal.custom_analyses:detach_existing_sql_edges", "kwargs": {"analysis_id": "<analysis:Top rented films>"}, "tables": ["custom_analysis__sql"]}),
    (SEM, {"call": "auto_ontology.dal.custom_analyses:delete_custom_analysis_node", "kwargs": {"analysis_id": "<analysis:Top rented films>"}, "tables": ["custom_analysis", "custom_analysis__sql", "sql_query", "sql_query__table", "sql_query__column"]}),
    (SEM, {"call": "auto_ontology.dal.pql_analyses:upsert_pql_analysis_node", "kwargs": {"analysis_id": "<pql:Customer churn>", "name": "churn", "description": "d", "pql": "PREDICT x"}, "tables": ["pql_analysis"]}),
    (SEM, {"call": "auto_ontology.dal.pql_analyses:upsert_pql_analysis_node", "kwargs": {"analysis_id": "new-pql", "name": "new", "description": "d", "pql": "PREDICT y"}, "tables": ["pql_analysis"]}),
    (SEM, {"call": "auto_ontology.dal.pql_analyses:delete_pql_analysis_node", "kwargs": {"analysis_id": "<pql:Customer churn>"}, "tables": ["pql_analysis"]}),
    (SEM, {"call": "auto_ontology.dal.reset:delete_semantic_layer", "kwargs": {"database_name": "pagila"}, "tables": ["*"]}),
    # these delete what the DAL capture looks up (a whole database): compare the driver's table dumps
    (SEM, {"call": "auto_ontology.dal.reset:delete_data_layer", "kwargs": {"database_name": "chinook"}, "tables": ["*"]}, {"WD_TOLERANT": "1"}),
    (SEM, {"call": "auto_ontology.dal.reset:delete_all_data", "kwargs": {}, "tables": ["*"]}, {"WD_TOLERANT": "1"}),
    # the oracle's own self-test: MUST be DIFFERENT
    (WC + "writes_data:vacuity_check", {"table_id": FILM}),
]

EXPECTED_DIFFERENT = {"writes_data:vacuity_check", "writes_data:semantic_fk_join_default"}


def _run(i: int, case: tuple, mode: str, out: Path) -> tuple:
    target, kwargs = case[0], case[1]
    env = {**os.environ, "NIDRA_WRITES": mode, **(case[2] if len(case) > 2 else {})}
    t0 = time.time()
    p = subprocess.run([sys.executable, "-m", "dev_tools.fixtures.write_diff", target, "--kwargs", json.dumps(kwargs)],
                       env=env, capture_output=True, text=True)
    (out / f"{i:03d}-{mode}.log").write_text(p.stdout + "\n--- stderr ---\n" + p.stderr)
    verdict = next((ln for ln in p.stdout.splitlines() if ln.startswith(("SAME", "DIFFERENT"))), None) or f"FAILED rc={p.returncode}"
    return i, mode, target.split(".")[-1], verdict, round(time.time() - t0)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--modes", default="sparql,commit")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--out", default=os.path.join(os.environ.get("TMPDIR", "/tmp"), "write-diff-all"))
    ap.add_argument("--only", help="comma-separated case numbers")
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    modes = args.modes.split(",")
    only = set(args.only.split(",")) if args.only else None
    work = [(i, c, m) for i, c in enumerate(CASES) for m in modes if only is None or str(i) in only]
    got: dict[tuple[int, str], str] = {}
    with cf.ThreadPoolExecutor(args.jobs) as ex:
        for fut in cf.as_completed([ex.submit(_run, i, c, m, out) for i, c, m in work]):
            i, mode, name, verdict, secs = fut.result()
            got[(i, mode)] = verdict
            print(f"{i}\t{mode}\t{name}\t{verdict[:80]}\t{secs}s", flush=True)
    bad = 0
    for i in sorted({i for i, _ in got}):
        name = CASES[i][0].split(".")[-1]
        kinds = {got[(i, m)].split(":")[0].split(" ")[0] for m in modes if (i, m) in got}
        want = "DIFFERENT" if name in EXPECTED_DIFFERENT else "SAME"
        if kinds != {want}:
            bad += 1
            print(f"UNEXPECTED {i} {name}: " + " | ".join(f"{m}={got.get((i, m), '-')[:60]}" for m in modes))
    print(f"{len({i for i, _ in got})} cases x {len(modes)} modes: {'all as expected' if not bad else f'{bad} unexpected'}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
