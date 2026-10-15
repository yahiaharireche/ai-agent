# Looks at every validation file and writes down what format it really is.
# The files change format from year to year, so we check each one instead of
# assuming. The result goes to pipeline/inventory.csv.
#
# Run from the project root:  .venv/bin/python pipeline/profile_files.py

import csv
import glob
import os

import openpyxl

FOLDER = "data/reseau-ferre"

# ** means "look in subfolders too", because the files are nested
files = glob.glob(FOLDER + "/**/*.csv", recursive=True)
files = files + glob.glob(FOLDER + "/**/*.txt", recursive=True)
files = files + glob.glob(FOLDER + "/**/*.xlsx", recursive=True)
files.sort()

print("Found " + str(len(files)) + " files")

results = []

for path in files:
    print("reading " + path)

    name = os.path.basename(path)
    size_mb = round(os.path.getsize(path) / 1000000, 1)

    # Excel files are not text, so they need a different reader.
    # There is no encoding or separator to find: the cells are already
    # separate, and openpyxl handles the decoding for us.
    if path.endswith(".xlsx"):
        # read_only means openpyxl gives us one row at a time instead of
        # loading the whole sheet into memory
        book = openpyxl.load_workbook(path, read_only=True)
        sheet = book.active

        columns = []
        n_rows = 0

        for row in sheet.rows:
            if n_rows == 0:
                # the first row holds the column names
                for cell in row:
                    columns.append(str(cell.value))
            n_rows = n_rows + 1

        book.close()
        n_rows = n_rows - 1  # the header is not data

        results.append([name, size_mb, "excel", "", "", n_rows, "|".join(columns)])
        continue

    # --- which encoding? ---
    # Some files start with a few hidden bytes that say how they are encoded.
    first_bytes = open(path, "rb").read(3)

    if first_bytes[0:2] == b"\xff\xfe":
        encoding = "utf-16"
    elif first_bytes[0:2] == b"\xfe\xff":
        encoding = "utf-16"
    elif first_bytes[0:3] == b"\xef\xbb\xbf":
        encoding = "utf-8-sig"
    else:
        encoding = "utf-8"

    # --- which separator? ---
    # Read the first line and see whether tabs or semicolons are more common.
    file = open(path, encoding=encoding, errors="replace")
    header = file.readline()

    if header.count("\t") > header.count(";"):
        separator = "\t"
        separator_name = "tab"
    else:
        separator = ";"
        separator_name = "semicolon"

    # --- which column names? ---
    columns = header.strip().split(separator)

    # --- comma or dot for decimals? ---
    # Look at the first 20 data rows for a value like 1,5
    decimal = "."
    checked = 0

    for line in file:
        values = line.strip().split(separator)
        for value in values:
            if value.count(",") == 1:
                left = value.split(",")[0]
                right = value.split(",")[1]
                if left.isdigit() and right.isdigit():
                    decimal = ","
        checked = checked + 1
        if checked == 20:
            break

    file.close()

    # --- how many rows? ---
    n_rows = 0
    file = open(path, encoding=encoding, errors="replace")
    for line in file:
        n_rows = n_rows + 1
    file.close()
    n_rows = n_rows - 1  # the first line is the header, not data

    results.append([
        name,
        size_mb,
        encoding,
        separator_name,
        decimal,
        n_rows,
        "|".join(columns),
    ])

# --- save everything to one file ---
output = open("pipeline/inventory.csv", "w", newline="", encoding="utf-8")
writer = csv.writer(output)
writer.writerow(["filename", "size_mb", "encoding", "separator", "decimal", "n_rows", "columns"])

for row in results:
    writer.writerow(row)

output.close()

print("Wrote pipeline/inventory.csv")
