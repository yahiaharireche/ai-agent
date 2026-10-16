# Loads model/quality_flags.csv into the database as the quality_flags table,
# adding how many rows and taps each warning actually covers, and creates
# warnings_between(start, end): the warnings that apply to a period.
#
# The CSV is written by hand, so it can go stale or be wrong. Each warning is
# checked against the final tables, and the build stops if:
#   - a warning matches no rows (it describes nothing, or its condition is broken)
#   - it also matches rows outside its stated period (the period is wrong, so a
#     question about that outside period would miss the warning)
#   - its stated period is more than a month wider than the rows it matches (the
#     warning would then be shown for years it does not affect)
#
# Run from the project root, after build_facts.py:
#   .venv/bin/python model/build_quality_flags.py

import csv

import duckdb

FLAGS = "model/quality_flags.csv"
DATABASE = "data/model/ridership.duckdb"

db = duckdb.connect(DATABASE)
results = []
problems = []

for flag in csv.DictReader(open(FLAGS, encoding="utf-8")):
    table = flag["table"]
    columns = []
    for column in db.sql("DESCRIBE " + table).fetchall():
        columns.append(column[0])

    # Daily tables have a date; hourly ones only a period.
    if "date" in columns:
        in_period = ("date BETWEEN '" + flag["start_date"] + "' AND '" +
                     flag["end_date"] + "'")
        first_last = "MIN(date), MAX(date)"
    else:
        in_period = ("period_start >= '" + flag["start_date"] +
                     "' AND period_end <= '" + flag["end_date"] + "'")
        first_last = "MIN(period_start), MAX(period_end)"

    # Hourly tables hold percentages, not counts, so they have no taps.
    taps = "SUM(taps)" if "taps" in columns else "NULL"

    rows, taps_affected, first, last = db.sql(
        "SELECT COUNT(*), " + taps + ", " + first_last + " FROM " + table +
        " WHERE " + in_period + " AND (" + flag["condition"] + ")").fetchone()

    outside = db.sql(
        "SELECT COUNT(*) FROM " + table +
        " WHERE NOT (" + in_period + ") AND (" + flag["condition"] + ")").fetchone()[0]

    if rows == 0:
        problems.append(flag["flag_id"] + ": matches no rows")
    # A period much wider than the data would attach the warning to years it
    # does not affect, which is a false warning. Allow a month of slack.
    if rows > 0:
        too_early = db.sql("SELECT DATE '" + flag["start_date"] + "' < DATE '" +
                           str(first) + "' - INTERVAL 31 DAY").fetchone()[0]
        too_late = db.sql("SELECT DATE '" + flag["end_date"] + "' > DATE '" +
                          str(last) + "' + INTERVAL 31 DAY").fetchone()[0]
        if too_early or too_late:
            problems.append(flag["flag_id"] + ": stated period " + flag["start_date"] +
                            ".." + flag["end_date"] + " is wider than the data, " +
                            str(first) + ".." + str(last))
    if outside > 0:
        problems.append(flag["flag_id"] + ": " + str(outside) +
                        " matching rows fall outside " + flag["start_date"] +
                        ".." + flag["end_date"])

    print("%-36s %-14s %10s rows %15s taps  seen %s..%s" %
          (flag["flag_id"], flag["affects"], format(rows, ","),
           "-" if taps_affected is None else format(int(taps_affected), ","),
           first, last))

    flag["rows_affected"] = rows
    flag["taps_affected"] = taps_affected
    results.append(flag)

if problems:
    raise SystemExit("warnings do not match the data:\n  " + "\n  ".join(problems))

db.sql("""
    CREATE OR REPLACE TABLE quality_flags (
        flag_id VARCHAR, "table" VARCHAR, start_date DATE, end_date DATE,
        affects VARCHAR, description VARCHAR, condition VARCHAR,
        rows_affected BIGINT, taps_affected BIGINT)
""")
for flag in results:
    db.execute("INSERT INTO quality_flags VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
               [flag["flag_id"], flag["table"], flag["start_date"], flag["end_date"],
                flag["affects"], flag["description"], flag["condition"],
                flag["rows_affected"], flag["taps_affected"]])

# The warnings that apply to a period: any warning whose period overlaps it.
# Written once here so the AI never has to get the overlap logic right itself.
db.sql("""
    CREATE OR REPLACE MACRO warnings_between(period_start, period_end) AS TABLE
    SELECT flag_id, "table", affects, start_date, end_date, description,
           rows_affected, taps_affected
    FROM quality_flags
    WHERE start_date <= CAST(period_end AS DATE)
      AND end_date >= CAST(period_start AS DATE)
    ORDER BY affects, flag_id
""")

db.close()
print("\nwrote quality_flags (" + str(len(results)) + " warnings) and "
      "warnings_between() into " + DATABASE)
