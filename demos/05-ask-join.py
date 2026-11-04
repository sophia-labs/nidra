"""Parity: questions that need joins (and a bridge table)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
from demo import Demo, has_number, has_text  # noqa: E402
from questions import run  # noqa: E402

d = Demo("05-ask-join", "NL question: joins", "Joins across related tables, found from foreign keys and join paths.",
         "README.md § Key Features; skills/nvidia-ontology-management § Semantic relationships")


def all_pairs(rows, truth):
    missing = [f"{k}={v}" for k, v in truth if not (has_text(rows, str(k)) and has_number(rows, float(v), 0))]
    return (not missing, "all names and counts match" if not missing else f"missing or wrong: {missing}")


run(d, [
    {"db": "chinook", "question": "Which 3 artists have the most tracks, and how many tracks does each have?",
     "truth_sql": "SELECT ar.Name, count(*) FROM Track t JOIN Album al ON al.AlbumId=t.AlbumId JOIN Artist ar ON ar.ArtistId=al.ArtistId GROUP BY 1 ORDER BY 2 DESC LIMIT 3",
     "grade": all_pairs},
    {"db": "pagila", "question": "Which 3 film categories have the most films, and how many films are in each?",
     "truth_sql": "SELECT c.name, count(*) FROM film_category fc JOIN category c ON c.category_id=fc.category_id GROUP BY 1 ORDER BY 2 DESC LIMIT 3",
     "grade": all_pairs},
])
d.finish()
