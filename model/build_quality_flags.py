# Turns model/quality_flags.csv into data/model/quality_flags.parquet, adding
# how many rows and validations each flag actually covers.
#
# The CSV is written by hand, so it can go stale or be wrong. Each flag is
# checked against the data, and the build stops if:
#   - a flag matches no rows (it describes nothing, or its condition is broken)
#   - its condition also matches rows outside its stated period (the period
#     is wrong, so an answer about that outside period would miss the caveat)
#
# Run from the project root, after build_dimensions.py:
#   .venv/bin/python model/build_quality_flags.py

import csv
import os

import duckdb

FLAGS = "model/quality_flags.csv"
OUTPUT = "data/model/quality_flags.parquet"

db = duckdb.connect()
db.sql("CREATE VIEW rail_counts AS SELECT * FROM 'data/parquet/rail_counts.parquet'")
db.sql("CREATE VIEW surface_counts AS SELECT * FROM 'data/parquet/surface_counts.parquet'")

results = []
problems = []

for flag in csv.DictReader(open(FLAGS, encoding="utf-8")):
    in_period = ("jour BETWEEN '" + flag["start_date"] + "' AND '" +
                 flag["end_date"] + "'")

    rows, validations, first, last = db.sql(
        "SELECT COUNT(*), SUM(TRY_CAST(nb_vald AS BIGINT)), MIN(jour), MAX(jour)"
        " FROM " + flag["table"] +
        " WHERE " + in_period + " AND (" + flag["condition"] + ")").fetchone()

    outside = db.sql(
        "SELECT COUNT(*) FROM " + flag["table"] +
        " WHERE NOT (" + in_period + ") AND (" + flag["condition"] + ")").fetchone()[0]

    if rows == 0:
        problems.append(flag["flag_id"] + ": matches no rows")
    if outside > 0:
        problems.append(flag["flag_id"] + ": " + str(outside) +
                        " matching rows fall outside " + flag["start_date"] +
                        ".." + flag["end_date"])

    print("%-34s %-14s %10s rows %15s validations  seen %s..%s" %
          (flag["flag_id"], flag["affects"], format(rows, ","),
           format(int(validations or 0), ","), first, last))

    flag["rows_affected"] = rows
    flag["validations_affected"] = int(validations or 0)
    results.append(flag)

if problems:
    raise SystemExit("quality flags do not match the data:\n  " + "\n  ".join(problems))

os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
db.sql("CREATE TABLE flags (flag_id VARCHAR, \"table\" VARCHAR, start_date DATE,"
       " end_date DATE, affects VARCHAR, description VARCHAR, condition VARCHAR,"
       " rows_affected BIGINT, validations_affected BIGINT)")
for flag in results:
    db.execute("INSERT INTO flags VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
               [flag["flag_id"], flag["table"], flag["start_date"], flag["end_date"],
                flag["affects"], flag["description"], flag["condition"],
                flag["rows_affected"], flag["validations_affected"]])
db.sql("COPY flags TO '" + OUTPUT + "' (FORMAT PARQUET)")
print("\nwrote " + OUTPUT + " (" + str(len(results)) + " flags)")
