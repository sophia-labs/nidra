"""Print CONNECTION_STRINGS for the side engine: Pagila on the host's Postgres
and Chinook from the fork's SQLite file (credentials from the fork's .env)."""
import os
import urllib.parse as u

pw = u.quote(os.environ["POSTGRES_PASSWORD"], safe="")
user = u.quote(os.environ["POSTGRES_USER"], safe="")
port = os.environ.get("POSTGRES_PORT", "5432")
fork = os.environ["FORK_DIR"]
print(f"postgresql://{user}:{pw}@localhost:{port}/pagila,sqlite:///{fork}/nidra/data/chinook.sqlite")
