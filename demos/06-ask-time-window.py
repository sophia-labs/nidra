"""Parity: questions bounded by a time window."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
from demo import Demo, has_number  # noqa: E402
from questions import run  # noqa: E402

d = Demo("06-ask-time-window", "NL question: a time window", "Date filters from plain language.",
         "README.md § Key Features; docs/mcp.md (\"what about last month?\")")

run(d, [
    {"db": "chinook", "question": "What was the total invoice revenue in the first quarter of 2023?",
     "truth_sql": "SELECT round(sum(Total),2) FROM Invoice WHERE InvoiceDate >= '2023-01-01' AND InvoiceDate < '2023-04-01'",
     "grade": lambda rows, t: (has_number(rows, float(t[0][0])), f"expected {t[0][0]}")},
    {"db": "pagila", "question": "How many rentals were made on May 25 and May 26, 2022, together?",
     "truth_sql": "SELECT count(*) FROM rental WHERE rental_date >= '2022-05-25' AND rental_date < '2022-05-27'",
     "grade": lambda rows, t: (has_number(rows, float(t[0][0]), 0), f"expected {t[0][0]}")},
])
d.finish()
