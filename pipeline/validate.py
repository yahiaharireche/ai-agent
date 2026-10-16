# Checks the clean files in data/clean/ against what the profiler recorded.
#
# This is deliberately a separate script from the loader. A loader that checks
# its own work will tell you it succeeded; an independent check can disagree.
# It is also the script that becomes a CI gate later.
#
# Run from the project root:  .venv/bin/python pipeline/validate.py

import csv
import os

import duckdb

INVENTORY = "pipeline/inventory.csv"

# What IDFM writes instead of a count below five, to avoid identifying anyone.
SUPPRESSED = "Moins de 5"

# Each group is a set of clean files that share a schema, so they can be
# queried as one table. Files are matched on a lowercased substring rather
# than a glob, because the source filenames are inconsistent: 2018_S2_NB_Fer
# and "2023_S1_NB_FER .txt" would both be missed by a *NB_FER.csv pattern.
GROUPS = [
    ("rail counts", "reseau-ferre", "nb_fer"),
    ("rail hourly", "reseau-ferre", "profil"),
    ("surface counts", "reseau-surface", "nb_surface"),
    ("surface hourly", "reseau-surface", "profil"),
]

db = duckdb.connect()
failures = 0


def check(label, passed, detail):
    """Print one result line and remember whether anything failed."""
    global failures
    if passed:
        print("  PASS  " + label + "   " + detail)
    else:
        print("  FAIL  " + label + "   " + detail)
        failures = failures + 1


def find_files(dataset, keyword):
    """Clean files in this dataset whose name contains keyword."""
    folder = "data/clean/" + dataset
    found = []
    for name in sorted(os.listdir(folder)):
        if keyword in name.lower():
            found.append(folder + "/" + name)
    return found


def source(files):
    """A DuckDB expression reading this exact list of files as one table.

    The format is stated rather than auto-detected. DuckDB samples the first
    20,480 rows to guess, and in 2023_T1_NB_SURFACE the first quoted value is
    at line 312,143 - so it concluded the file had no quoting and then split
    a station name containing a comma into an extra column.
    """
    quoted = []
    for path in files:
        quoted.append("'" + path + "'")
    return ("read_csv([" + ", ".join(quoted) + "], union_by_name=true,"
            " delim=',', quote='\"', escape='\"', header=true)")


# How many rows the profiler counted in each source file.
expected_rows = {}
for item in csv.DictReader(open(INVENTORY, encoding="utf-8")):
    if item["n_rows"]:
        expected_rows[item["filename"]] = int(item["n_rows"])

for label, dataset, keyword in GROUPS:
    print("\n=== " + label + " ===")

    files = find_files(dataset, keyword)
    table = source(files)

    try:
        rows = db.sql("SELECT source_file, COUNT(*) AS n FROM " + table +
                      " GROUP BY source_file").fetchall()
    except Exception as error:
        check("readable", False, str(error)[:150])
        continue

    # --- 1. every file has the number of rows the profiler counted ---
    bad = []
    total = 0
    for source_file, n in rows:
        total = total + n
        if expected_rows.get(source_file) != n:
            bad.append(source_file + " got " + str(n) +
                       " expected " + str(expected_rows.get(source_file)))

    check("row counts", len(bad) == 0 and len(rows) == len(files),
          str(len(rows)) + " files, " + format(total, ",") + " rows" +
          ("" if not bad else "  " + "; ".join(bad[:3])))

    columns = db.sql("DESCRIBE SELECT * FROM " + table).fetchall()
    names = []
    for column in columns:
        names.append(column[0])

    # --- 2. dates are real and inside the period we were given ---
    if "jour" in names:
        lo, hi, nulls = db.sql(
            "SELECT MIN(jour), MAX(jour), COUNT(*) FILTER (WHERE jour IS NULL)"
            " FROM " + table).fetchone()
        in_range = str(lo) >= "2015-01-01" and str(hi) <= "2025-12-31"
        check("date range", in_range and nulls == 0,
              str(lo) + " to " + str(hi) + ", " + str(nulls) + " null")

        # A range check over the whole period is weak: a date can be wrong and
        # still land inside 2015-2025. Each file covers one year and says so in
        # its name, so that is the stronger test.
        per_file = db.sql("SELECT source_file, MIN(jour), MAX(jour) FROM " +
                          table + " GROUP BY source_file").fetchall()
        wrong = []
        for source_file, lo2, hi2 in per_file:
            year = source_file[0:4]
            if not year.isdigit():
                continue
            if str(lo2)[0:4] != year or str(hi2)[0:4] != year:
                wrong.append(source_file + " has " + str(lo2) + ".." + str(hi2))

        check("dates match filename year", len(wrong) == 0,
              str(len(per_file)) + " files checked" +
              ("" if not wrong else "   " + "; ".join(wrong[:3])))

    # --- 3. the measure is present and never negative ---
    measure = "nb_vald"
    if measure not in names:
        measure = "pourc_validations"

    if measure in names:
        # SUPPRESSED is the marker IDFM puts in place of a count below five.
        # It is real data, not a defect in our loading, so it must not fail
        # the check - but anything else that will not parse as a number is a
        # value we have silently lost, and must.
        nulls, negatives, unparseable, suppressed = db.sql(
            "SELECT COUNT(*) FILTER (WHERE " + measure + " IS NULL),"
            "       COUNT(*) FILTER (WHERE TRY_CAST(" + measure +
            " AS DOUBLE) < 0),"
            "       COUNT(*) FILTER (WHERE TRY_CAST(" + measure +
            " AS DOUBLE) IS NULL AND CAST(" + measure +
            " AS VARCHAR) <> '" + SUPPRESSED + "'),"
            "       COUNT(*) FILTER (WHERE CAST(" + measure + " AS VARCHAR) = '" +
            SUPPRESSED + "') FROM " + table).fetchone()

        check(measure, nulls == 0 and negatives == 0 and unparseable == 0,
              str(nulls) + " null, " + str(negatives) + " negative, " +
              str(unparseable) + " unparseable")

        # Not a pass or fail: a number the reader has to know about.
        print("  NOTE  suppressed   " + format(suppressed, ",") +
              " rows recorded as '" + SUPPRESSED + "'")

    # --- 4. no mangled characters from a wrong encoding ---
    text_column = ""
    for name in names:
        if name.startswith("libelle"):
            text_column = name

    if text_column:
        mangled = db.sql(
            "SELECT COUNT(*) FROM " + table +
            " WHERE " + text_column + " LIKE '%�%'").fetchone()[0]
        check("encoding", mangled == 0,
              str(mangled) + " mangled values in " + text_column)

    # --- 5. which original spellings of the key column appear ---
    if "zone_id_source" in names:
        spellings = db.sql("SELECT DISTINCT zone_id_source FROM " + table +
                           " ORDER BY 1").fetchall()
        found = []
        for spelling in spellings:
            if spelling[0]:
                found.append(spelling[0])
        check("key column", True, ", ".join(found))

print("\n" + ("All checks passed" if failures == 0
              else str(failures) + " check(s) FAILED"))
