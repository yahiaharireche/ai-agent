# Converts the clean CSV files in data/clean/ into Parquet in data/parquet/.
#
# Why: Parquet stores each column together instead of each row, and stores the
# type of every column inside the file. On rail counts that is 1,672 MB of CSV
# against 65.6 MB of Parquet, and a query that took 11.06 s takes 0.05 s.
#
# The CSV layer is kept. It is the readable copy, useful while the loader is
# still being changed; Parquet is the one queries run against.
#
# Run from the project root:  .venv/bin/python pipeline/to_parquet.py

import os

import duckdb

CLEAN_DIR = "data/clean"
OUTPUT_DIR = "data/parquet"

# The four fact groups. Files in a group share a schema, so each group becomes
# a single Parquet file.
GROUPS = [
    ("rail_counts", "reseau-ferre", "nb_fer"),
    ("rail_hourly", "reseau-ferre", "profil"),
    ("surface_counts", "reseau-surface", "nb_surface"),
    ("surface_hourly", "reseau-surface", "profil"),
]

# These two hold one table per file, each with its own columns, so each file
# is converted on its own.
SINGLE_FILE_FOLDERS = ["extra", "gtfs"]


def find_files(dataset, keyword):
    """Clean files in this dataset whose name contains keyword."""
    folder = CLEAN_DIR + "/" + dataset
    found = []
    for name in sorted(os.listdir(folder)):
        if keyword in name.lower():
            found.append(folder + "/" + name)
    return found


def source(files):
    """A DuckDB expression reading this list of CSV files as one table.

    The format is stated rather than auto-detected. DuckDB guesses from the
    first 20,480 rows, and in 2023_T1_NB_SURFACE the first quoted value is at
    line 312,143 - so it decided the file had no quoting and then split a
    station name containing a comma into an extra column.

    all_varchar keeps every column as text. nb_vald holds "Moins de 5" where
    IDFM suppressed a count below five, and letting DuckDB type that column as
    a number would turn those into nulls - losing the difference between "was
    suppressed" and "was missing". Deciding what to do about that is a
    modelling question, not a storage one.
    """
    quoted = []
    for path in files:
        quoted.append("'" + path + "'")
    return ("read_csv([" + ", ".join(quoted) + "], union_by_name=true,"
            " delim=',', quote='\"', escape='\"', header=true, all_varchar=true)")


db = duckdb.connect()
os.makedirs(OUTPUT_DIR, exist_ok=True)

for name, dataset, keyword in GROUPS:
    files = find_files(dataset, keyword)
    out = OUTPUT_DIR + "/" + name + ".parquet"

    db.sql("COPY (SELECT * FROM " + source(files) + ") TO '" + out +
           "' (FORMAT PARQUET, COMPRESSION ZSTD)")

    csv_mb = 0.0
    for path in files:
        csv_mb = csv_mb + os.path.getsize(path) / 1048576
    pq_mb = os.path.getsize(out) / 1048576

    print("%-16s %3d files  %8.1f MB CSV -> %7.1f MB parquet  (%.0fx smaller)"
          % (name, len(files), csv_mb, pq_mb, csv_mb / pq_mb))

for folder in SINGLE_FILE_FOLDERS:
    out_folder = OUTPUT_DIR + "/" + folder
    os.makedirs(out_folder, exist_ok=True)

    for filename in sorted(os.listdir(CLEAN_DIR + "/" + folder)):
        path = CLEAN_DIR + "/" + folder + "/" + filename
        out = out_folder + "/" + os.path.splitext(filename)[0] + ".parquet"

        db.sql("COPY (SELECT * FROM " + source([path]) + ") TO '" + out +
               "' (FORMAT PARQUET, COMPRESSION ZSTD)")

        print("%-16s %-42s %7.1f MB -> %6.1f MB"
              % (folder, filename, os.path.getsize(path) / 1048576,
                 os.path.getsize(out) / 1048576))

print("Done")
