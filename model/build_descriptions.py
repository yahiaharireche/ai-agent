# Attaches the plain-English descriptions in model/descriptions.csv to every
# table and column in the database, so the AI can read what each one means.
#
# The build stops if a table or column has no description, or if a
# description names a column that does not exist. A new column cannot slip in
# undocumented, and a renamed one cannot leave a stale description behind.
#
# Run from the project root, after build_quality_flags.py:
#   .venv/bin/python model/build_descriptions.py

import csv

import duckdb

DESCRIPTIONS = "model/descriptions.csv"
DATABASE = "data/model/ridership.duckdb"

db = duckdb.connect(DATABASE)

described = {}
for row in csv.DictReader(open(DESCRIPTIONS, encoding="utf-8")):
    described[(row["table"], row["column"])] = row["description"]

actual = set()
for (table,) in db.sql("SELECT table_name FROM information_schema.tables").fetchall():
    actual.add((table, ""))
    for column in db.sql("DESCRIBE " + table).fetchall():
        actual.add((table, column[0]))

missing = sorted(actual - set(described))
stale = sorted(set(described) - actual)
if missing or stale:
    lines = []
    for table, column in missing:
        lines.append("no description: " + table + ("." + column if column else ""))
    for table, column in stale:
        lines.append("described but does not exist: " + table +
                     ("." + column if column else ""))
    raise SystemExit("\n".join(lines))


def quote(text):
    """Put text inside SQL single quotes, doubling any quote inside it."""
    return "'" + text.replace("'", "''") + "'"


for (table, column), text in sorted(described.items()):
    if column:
        db.sql("COMMENT ON COLUMN " + table + '."' + column + '" IS ' + quote(text))
    else:
        db.sql("COMMENT ON TABLE " + table + " IS " + quote(text))

db.close()
print("described " + str(len(described)) + " tables and columns in " + DATABASE)
