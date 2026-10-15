# Looks at every data file and writes down what format it really is.
# The files change format from year to year and from source to source, so we
# check each one instead of assuming. The result goes to pipeline/inventory.csv.
#
# Files are inspected in parallel: each one is independent, so nothing is
# shared between workers and the order they finish in does not matter.
#
# Run from the project root:  .venv/bin/python pipeline/profile_files.py

import codecs
import csv
import glob
import multiprocessing
import os

import openpyxl

FOLDERS = [
    "data/reseau-ferre",
    "data/reseau-surface",
    "data/extra",
    "data/gtfs",
]

OUTPUT = "pipeline/inventory.csv"
WORKERS = 8

# Candidate column separators, with the name we write in the report.
SEPARATOR_NAMES = {";": "semicolon", "\t": "tab", ",": "comma"}


def detect_encoding(path):
    """Work out how a text file is encoded.

    A byte order mark is the file stating its own encoding, so it wins.
    Without one the file is either utf-8 or an older single-byte encoding
    like cp1252. The two cannot be told apart by guessing, but utf-8 has
    strict rules and cp1252 does not: if strict utf-8 decoding succeeds the
    file is utf-8, and if it fails the file is not. Note the test only works
    in that direction - cp1252 accepts almost any byte, so "it decoded" tells
    us nothing on its own.
    """
    first_bytes = open(path, "rb").read(3)

    if first_bytes[0:2] == b"\xff\xfe" or first_bytes[0:2] == b"\xfe\xff":
        return "utf-16"
    if first_bytes[0:3] == b"\xef\xbb\xbf":
        return "utf-8-sig"

    # Decode in chunks so a large file is never held in memory at once. The
    # incremental decoder remembers a half-finished character between chunks.
    decoder = codecs.getincrementaldecoder("utf-8")()
    file = open(path, "rb")

    while True:
        chunk = file.read(1000000)
        if not chunk:
            break
        try:
            decoder.decode(chunk)
        except UnicodeDecodeError:
            file.close()
            return "cp1252"

    file.close()
    return "utf-8"


def profile_one(job):
    """Inspect a single file and return one inventory row.

    This runs in a worker process, so it must not depend on anything the
    other files did. It takes a path and gives back a list.
    """
    dataset, path = job

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

        return [dataset, name, path, size_mb, "excel", "", "",
                n_rows, "|".join(columns)]

    # --- which encoding? ---
    encoding = detect_encoding(path)

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
    # Counted with csv.reader, not by counting lines. A value may contain
    # a line break inside quotes, which is one row but two lines:
    # referentiel-des-lignes.csv has 55 of them.
    n_rows = 0
    file = open(path, encoding=encoding, errors="replace")
    reader = csv.reader(file, delimiter=separator)
    for row in reader:
        n_rows = n_rows + 1
    file.close()
    n_rows = n_rows - 1  # the first line is the header, not data

    return [dataset, name, path, size_mb, encoding, separator_name,
            decimal, n_rows, "|".join(columns)]


# Everything below runs only in the main process. Without this guard each
# worker would import this file and run it again, starting more workers.
if __name__ == "__main__":
    jobs = []

    for folder in FOLDERS:
        # ** means "look in subfolders too", because some files are nested
        files = glob.glob(folder + "/**/*.csv", recursive=True)
        files = files + glob.glob(folder + "/**/*.txt", recursive=True)
        files = files + glob.glob(folder + "/**/*.xlsx", recursive=True)
        files.sort()

        dataset = os.path.basename(folder)
        print(dataset + ": " + str(len(files)) + " files")

        for path in files:
            jobs.append((dataset, path))

    results = []
    done = 0

    pool = multiprocessing.Pool(WORKERS)

    # imap_unordered hands back each result as soon as that worker finishes,
    # so slow files do not hold up the ones behind them.
    for row in pool.imap_unordered(profile_one, jobs):
        done = done + 1
        print("[" + str(done) + "/" + str(len(jobs)) + "] " + row[1])
        results.append(row)

    pool.close()
    pool.join()

    # Workers finish out of order, so sort before writing.
    results.sort(key=lambda row: row[2])

    output = open(OUTPUT, "w", newline="", encoding="utf-8")
    writer = csv.writer(output)
    writer.writerow(["dataset", "filename", "path", "size_mb", "encoding",
                     "separator", "decimal", "n_rows", "columns"])

    for row in results:
        writer.writerow(row)

    output.close()

    print("Wrote " + OUTPUT + " (" + str(len(results)) + " files)")
