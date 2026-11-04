# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Modified by Sophia Labs for Nidra, 2026-11-03 (see NIDRA-NOTICE.md).

"""Read user-configured acronyms and custom prompts from the Auto Ontology metadata DB.

The frontend manages these via Prisma (`acronyms` and `prompts` tables in the
same Postgres instance the backend uses); the chat router pulls them on every
request and injects them into the text-to-SQL agent payload, so the client no
longer needs to ship them on the wire.
"""

from __future__ import annotations

import logging

import json
import os

import psycopg
from psycopg.rows import dict_row

from auto_ontology.infra.postgres import FRONTEND_SCHEMA, get_postgres_connection_string

logger = logging.getLogger(__name__)


def _rdf() -> bool:
    from auto_ontology.rdf.patch import enabled

    return enabled()


def fetch_acronyms() -> list[dict[str, str]]:
    """Return acronyms as ``[{"name": ..., "description": ...}, ...]``.

    Shape matches ``AgentState.acronyms`` / the dicts
    consumed by ``rules_to_text`` in the text-to-SQL pipeline.
    """
    if _rdf():
        # Sophia Labs fork: AO_ACRONYMS is a JSON list of {name, description}.
        try:
            return [dict(a) for a in json.loads(os.environ.get("AO_ACRONYMS") or "[]")]
        except ValueError:
            logger.exception("AO_ACRONYMS is not JSON; continuing with empty list")
            return []
    try:
        with psycopg.connect(
            get_postgres_connection_string(), connect_timeout=3
        ) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    f"SELECT name, description FROM {FRONTEND_SCHEMA}.acronyms ORDER BY created_at"
                )
                return [dict(row) for row in cur.fetchall()]
    except Exception:
        # Settings are best-effort: a DB hiccup here must not break chat.
        logger.exception("Failed to fetch acronyms; continuing with empty list")
        return []


def is_visualization_enabled() -> bool:
    """Instance-wide "Visualize SQL Results" toggle (Settings > Agent Settings).

    Reads the same ``configurations`` row the frontend's
    ``isVisualizationEnabled`` (Prisma) checks, so both sides agree on the
    setting without a round trip through the Next.js API. A missing row or a
    DB hiccup both count as enabled — the feature is on by default and a
    transient read failure shouldn't silently suppress every chart.
    """
    if _rdf():
        return os.environ.get("AO_VISUALIZATION_ENABLED", "true").lower() != "false"
    try:
        with psycopg.connect(
            get_postgres_connection_string(), connect_timeout=3
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT value FROM {FRONTEND_SCHEMA}.configurations "
                    "WHERE key = %s",
                    ("visualization_enabled",),
                )
                row = cur.fetchone()
        return row is None or row[0] != "false"
    except Exception:
        logger.exception("Failed to read visualization setting; defaulting to enabled")
        return True


def fetch_custom_prompts() -> str:
    """Return all stored prompts joined into one string (blank if none).

    The agent embeds this verbatim into the system prompt
    (see ``auto_ontology.retrieval.text_to_sql.main._build_state``).
    """
    if _rdf():
        return os.environ.get("AO_CUSTOM_PROMPTS", "")
    try:
        with psycopg.connect(
            get_postgres_connection_string(), connect_timeout=3
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT content FROM {FRONTEND_SCHEMA}.prompts")
                rows = cur.fetchall()
    except Exception:
        logger.exception("Failed to fetch custom prompts; continuing with empty string")
        return ""

    contents = [row[0] for row in rows if row[0]]
    return "\n\n".join(contents)
