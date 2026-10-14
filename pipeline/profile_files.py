"""Inspect every validation file and record its actual format.

The yearly files drift in encoding, delimiter, decimal separator and column
names. This script reads each one and writes what it really contains to
pipeline/inventory.csv, so the loader can be driven by facts instead of guesses.

Run from the project root:  .venv/bin/python pipeline/profile_files.py
"""

import csv
import os

DATA_DIR = "data/reseau-ferre"
OUTPUT = "pipeline/inventory.csv"

# Tried in order. The first one that decodes the sample wins.
ENCODINGS = ["utf-8-sig", "utf-8", "cp1252", "latin-1"]

# Counted in the header line; the most frequent one is the delimiter.
DELIMITERS = [";", ",", "\t", "|"]


def find_files(folder):
    """Return every data file under folder, deepest ones included."""
    found = []
    for current_folder, subfolders, filenames in os.walk(folder):
        for name in filenames:
            if name.lower().endswith((".csv", ".txt", ".xlsx")):
                found.append(os.path.join(current_folder, name))
    found.sort()
    return found


# A byte order mark is the file stating its own encoding. It must be checked
# before anything is tried, because cp1252 and latin-1 map almost every byte to
# some character and so never raise - they would "successfully" decode a UTF-16
# file into nonsense.
BOMS = [
    (b"\xff\xfe", "utf-16"),
    (b"\xfe\xff", "utf-16"),
    (b"\xef\xbb\xbf", "utf-8-sig"),
]


def detect_encoding(path):
    """Return this file's encoding, trusting a byte order mark if present."""
    sample = open(path, "rb").read(200000)

    for marker, encoding in BOMS:
        if sample.startswith(marker):
            return encoding

    for encoding in ENCODINGS:
        try:
            sample.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue
    return "unknown"


def detect_delimiter(header_line):
    """Return whichever candidate delimiter appears most in the header."""
    best = ""
    best_count = 0
    for delimiter in DELIMITERS:
        count = header_line.count(delimiter)
        if count > best_count:
            best = delimiter
            best_count = count
    return best


def detect_decimal(rows, columns):
    """Look for a value like 1,5 — a comma used as a decimal point.

    Only called once the delimiter is known, so a comma inside a field is
    genuinely part of the value and not a column separator.
    """
    for row in rows:
        for value in row:
            if value.count(",") == 1:
                left, right = value.split(",")
                if left.strip("-").isdigit() and right.isdigit():
                    return ","
    return "."


def count_lines(path, encoding):
    """Count data rows, excluding the header."""
    total = 0
    file = open(path, encoding=encoding, errors="replace")
    for line in file:
        total += 1
    file.close()
    return max(total - 1, 0)


def profile(path):
    """Return one inventory row describing this file."""
    result = {
        "path": path,
        "filename": os.path.basename(path),
        "size_mb": round(os.path.getsize(path) / 1024 / 1024, 2),
        "extension": os.path.splitext(path)[1].lower(),
        "encoding": "",
        "delimiter": "",
        "decimal": "",
        "n_columns": 0,
        "n_rows": 0,
        "columns": "",
        "error": "",
    }

    # .xlsx is a zip archive, not text. It needs openpyxl, so it is recorded
    # here and inspected separately.
    if result["extension"] == ".xlsx":
        result["error"] = "xlsx - needs openpyxl"
        return result

    result["encoding"] = detect_encoding(path)
    if result["encoding"] == "unknown":
        result["error"] = "could not decode"
        return result

    file = open(path, encoding=result["encoding"], errors="replace")
    header_line = file.readline()
    result["delimiter"] = detect_delimiter(header_line)

    # Re-read the header through the csv module so quoted fields are handled.
    reader = csv.reader([header_line], delimiter=result["delimiter"])
    columns = next(reader)
    columns = [c.strip() for c in columns]

    sample_rows = []
    reader = csv.reader(file, delimiter=result["delimiter"])
    for row in reader:
        sample_rows.append(row)
        if len(sample_rows) >= 50:
            break
    file.close()

    result["n_columns"] = len(columns)
    result["columns"] = "|".join(columns)
    result["decimal"] = detect_decimal(sample_rows, columns)
    result["n_rows"] = count_lines(path, result["encoding"])
    return result


files = find_files(DATA_DIR)
print("Found " + str(len(files)) + " files in " + DATA_DIR)

rows = []
for path in files:
    print("  reading " + path)
    rows.append(profile(path))

output_file = open(OUTPUT, "w", newline="", encoding="utf-8")
writer = csv.DictWriter(output_file, fieldnames=list(rows[0].keys()))
writer.writeheader()
writer.writerows(rows)
output_file.close()

print("Wrote " + OUTPUT)
