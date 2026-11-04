# SPDX-License-Identifier: Apache-2.0
"""Captures for the model-interchange lane: the export reads, whole catalog and
one database at a time, and the not-found / empty paths."""
from __future__ import annotations

from typing import Any


def capture(cap, ids: dict[str, Any]) -> None:
    from auto_ontology.dal import model_interchange as mi
    from auto_ontology.dal.datasources import fetch_databases

    run = cap.run
    databases = {row["name"]: row["id"] for row in fetch_databases()}

    run("model_interchange.fetch_export_rows__all", lambda: mi.fetch_export_rows([]))
    for name, db_id in sorted(databases.items()):
        run(f"model_interchange.fetch_export_rows__db_{name}", lambda d=db_id: mi.fetch_export_rows([d]))
    if len(databases) > 1:
        run(
            "model_interchange.fetch_export_rows__all_listed",
            lambda: mi.fetch_export_rows(sorted(databases.values())),
        )
    run("model_interchange.fetch_export_rows__unknown_db", lambda: mi.fetch_export_rows(["no-such-db"]))

    run("model_interchange.validate_database_ids__empty", lambda: mi.validate_database_ids([]))
    run(
        "model_interchange.validate_database_ids__known",
        lambda: mi.validate_database_ids(sorted(databases.values())),
    )
    def unknown():
        try:
            mi.validate_database_ids([ids["db_pagila"], "no-such-db"])
        except mi.UnknownDatabaseIdsError as exc:
            return {"raised": type(exc).__name__, "database_ids": exc.database_ids}
        return {"raised": None}

    run("model_interchange.validate_database_ids__unknown", unknown)

    # The assembled document, as the export endpoint builds it.
    def document(database_ids: list[str]):
        rows = mi.fetch_export_rows(database_ids)
        return mi.assemble_export_document(
            rows,
            dialect_by_db_name={},
            sql_column_resolver=mi.make_cached_sql_column_resolver(),
        ).model_dump(mode="json")

    run("model_interchange.assemble_export_document__all", lambda: document([]))
    run("model_interchange.assemble_export_document__pagila", lambda: document([ids["db_pagila"]]))
