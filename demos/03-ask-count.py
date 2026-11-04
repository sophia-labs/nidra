"""Parity: natural-language count over both sources."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
from demo import NIDRA, Demo, has_number  # noqa: E402
from questions import run  # noqa: E402

d = Demo("03-ask-count", "NL question: a count",
         "Natural-language querying of structured data: the question becomes SQL and runs on the connected database.",
         "README.md § Key Features; docs/mcp.md § ask_question")

num = lambda rows, t: (has_number(rows, t[0][0], 0), f"expected {t[0][0]}")  # noqa: E731
run(d, [
    {"db": "chinook", "question": "How many tracks are there in the Chinook database?",
     "truth_sql": "SELECT count(*) FROM Track", "grade": num},
    {"db": "pagila", "engine": NIDRA, "label": "Pagila on the LIVE engine (no Pagila connection)",
     "question": "How many films are in the Pagila catalog?",
     "truth_sql": "SELECT count(*) FROM film", "grade": num},
    {"db": "pagila", "question": "How many films are in the Pagila catalog?",
     "truth_sql": "SELECT count(*) FROM film", "grade": num},
])

d.finish(None, "Chinook on the live engine; Pagila only on the side engine (live engine has no Pagila connection)")
