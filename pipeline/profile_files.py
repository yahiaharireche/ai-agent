# Looks at every data file and writes down what format it really is.
# The files change format from year to year and from source to source, so we
# check each one instead of assuming. The result goes to pipeline/inventory.csv.
#
# Run from the project root:  .venv/bin/python pipeline/profile_files.py

import csv
import glob
import os

import openpyxl

FOLDERS = [
    "data/reseau-ferre",
    "data/reseau-surface",
    "data/extra",
    "data/gtfs",
]

OUTPUT = "pipeline/inventory.csv"

# Candidate column separators, with the name we write in the report.
SEPARATOR_NAMES = {";": "semicolon", "\t": "tab", ",": "comma"}

results = []

for folder in FOLDERS:
    # ** means "look in subfolders too", because some files are nested
    files = glob.glob(folder + "/**/*.csv", recursive=True)
    files = files + glob.glob(folder + "/**/*.txt", recursive=True)
    files = files + glob.glob(folder + "/**/*.xlsx", recursive=True)
    files.sort()

    dataset = os.path.basename(folder)
    print(dataset + ": " + str(len(files)) + " files")

    for path in files:
        print("  reading " + path)

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

            results.append([dataset, name, path, size_mb, "excel", "", "",
                            n_rows, "|".join(columns)])
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
        # Read the first line and count each candidate. A header with 8 columns
        # has 7 separators, so the most frequent character is the separator.
        file = open(path, encoding=encoding, errors="replace")
        header = file.readline()

        separator = ";"
        for candidate in SEPARATOR_NAMES:
            if header.count(candidate) > header.count(separator):
                separator = candidate

        separator_name = SEPARATOR_NAMES[separator]

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

        results.append([dataset, name, path, size_mb, encoding, separator_name,
                        decimal, n_rows, "|".join(columns)])

# --- save everything to one file ---
output = open(OUTPUT, "w", newline="", encoding="utf-8")
writer = csv.writer(output)
writer.writerow(["dataset", "filename", "path", "size_mb", "encoding",
                 "separator", "decimal", "n_rows", "columns"])

for row in results:
    writer.writerow(row)

output.close()

print("Wrote " + OUTPUT + " (" + str(len(results)) + " files)")
