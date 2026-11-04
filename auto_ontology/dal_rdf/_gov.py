# SPDX-License-Identifier: Apache-2.0
"""Helpers shared by the governance write ports (tags, zones, rules, groups).

Not a port module (leading underscore): the installer skips it.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy.exc import IntegrityError

from auto_ontology.dal_rdf._common import client, iri, select_rows, values, wrap
from auto_ontology.rdf.vocab import row_id, row_iri_single


def now() -> datetime:
    return datetime.now(timezone.utc)


def input_fold(name: str) -> str:
    """The input side of ``lower(trim(name)) = :name``: the DAL folds in Python."""
    return name.strip().lower()


def stored_fold(name: str | None) -> str:
    """The stored side: Postgres ``lower(TRIM(BOTH FROM name))`` trims spaces only."""
    return (name or "").strip(" ").lower()


def sort_key(name: str | None, row_id: str) -> tuple[str, str, str]:
    """``ORDER BY lower(name), id`` as a database with an ``en_US.utf8`` collation
    orders it: the first pass ignores spaces and punctuation, the full lowered
    string breaks ties, then the id. An approximation of glibc's collation that is
    exact for names made of ASCII letters, digits, spaces and punctuation."""
    lowered = (name or "").lower()
    return ("".join(ch for ch in lowered if ch.isalnum()), lowered, row_id)


def like_regex(pattern: str) -> re.Pattern[str]:
    """A SQL ``LIKE`` pattern (``%``, ``_``, backslash escape) as a regex, case-insensitive."""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            out.append(re.escape(pattern[i + 1]))
            i += 2
            continue
        out.append(".*" if ch == "%" else "." if ch == "_" else re.escape(ch))
        i += 1
    return re.compile("^" + "".join(out) + "$", re.DOTALL | re.IGNORECASE)


def ilike(text: str | None, pattern: str) -> bool:
    return text is not None and like_regex(pattern).match(text) is not None


def rows_by_ids(table: str, ids: Iterable[str], columns: list[str]) -> list[dict[str, Any]]:
    ids = list(dict.fromkeys(ids))
    if not ids:
        return []
    subjects = [row_iri_single(table, i) for i in ids]
    return select_rows(table, columns, extra=values("s", subjects))


def existing_ids(table: str, ids: Iterable[str]) -> set[str]:
    ids = list(dict.fromkeys(ids))
    if not ids:
        return set()
    rows = client().select(
        f"SELECT ?s WHERE {{ {wrap(f'{values(chr(115), [row_iri_single(table, i) for i in ids])} ?s a {iri(f'urn:ao:{table}')} .')} }}"
    )
    return {row_id(r["s"]) for r in rows}


def integrity_error(statement: str, constraint: str) -> IntegrityError:
    """The error Postgres would raise for a violated constraint."""
    return IntegrityError(statement, {}, Exception(f'violates constraint "{constraint}"'))
