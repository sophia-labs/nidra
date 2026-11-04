"""Parity: questions that only make sense through the glossary (synonyms, SQL attributes)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
from demo import Demo, has_number, has_text  # noqa: E402
from questions import run  # noqa: E402

d = Demo("07-ask-glossary-term", "NL question that needs a glossary term",
         "Ground: agents get trusted business meaning (terms, synonyms, reviewed SQL attributes).",
         "README.md (Ground); docs/mcp.md (search_terms, get_term_sql_attributes)")

res = run(d, [
    {"db": "pagila", "label": "'patrons' is a synonym of the term Customer",
     "question": "How many patrons are there?",
     "truth_sql": "SELECT count(*) FROM customer",
     "grade": lambda rows, t: (has_number(rows, float(t[0][0]), 0), f"expected {t[0][0]}")},
    {"db": "pagila", "label": "'customer lifetime value' is a SQL attribute of Customer",
     "question": "Which patron has the highest customer lifetime value, and what is it?",
     "truth_sql": "SELECT c.first_name || ' ' || c.last_name, sum(p.amount) FROM payment p JOIN customer c USING (customer_id) GROUP BY 1 ORDER BY 2 DESC LIMIT 1",
     "grade": lambda rows, t: (has_number(rows, float(t[0][1])) and (has_text(rows, t[0][0].split()[0]) or has_text(rows, t[0][0].split()[-1]) or has_number(rows, 0, -1)),
                              f"expected {t[0][0]} {t[0][1]}")},
    {"db": "chinook", "label": "'song' is a synonym of the term Track",
     "question": "How many songs are longer than five minutes?",
     "truth_sql": "SELECT count(*) FROM Track WHERE Milliseconds > 300000",
     "grade": lambda rows, t: (has_number(rows, float(t[0][0]), 0), f"expected {t[0][0]}")},
])
d.section("did the glossary reach the agent?")
for a in res:
    thoughts = " ".join(s.get("thought") or "" for s in a["steps"]).lower()
    d.log(f"- {a['question']!r}: thoughts mention glossary/term/synonym: "
          f"{any(w in thoughts for w in ('glossary', 'synonym', 'term', 'lifetime value', 'patron'))}")
d.finish()
