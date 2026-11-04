"""Parity: aggregates (group by, sum/avg)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
from demo import Demo, has_number, has_text  # noqa: E402
from questions import run  # noqa: E402

d = Demo("04-ask-aggregate", "NL question: aggregates", "Aggregates over one table, grouped.",
         "README.md § Key Features")


def all_pairs(rows, truth):
    missing = [f"{k}={v}" for k, v in truth if not (has_text(rows, str(k)) and has_number(rows, float(v)))]
    return (not missing, "all groups and values match" if not missing else f"missing or wrong: {missing}")


run(d, [
    {"db": "chinook", "question": "What is the total invoice revenue per billing country? Give the top 5 countries.",
     "truth_sql": "SELECT BillingCountry, round(sum(Total),2) FROM Invoice GROUP BY 1 ORDER BY 2 DESC LIMIT 5", "grade": all_pairs},
    {"db": "pagila", "question": "What is the average rental rate of films for each rating?",
     "truth_sql": "SELECT rating::text, round(avg(rental_rate),2) FROM film GROUP BY 1 ORDER BY 1", "grade": all_pairs},
])
d.finish()
