# Reads every data file using the format recorded in pipeline/inventory.csv,
# normalises it, and writes a clean copy to data/clean/.
#
# The rule for this stage: fix the FORMAT, never the MEANING. Column names are
# made consistent and values are tidied, but nothing is merged, corrected or
# interpreted. Where a column was renamed over the years, the original name is
# kept in zone_id_source so no evidence is thrown away.
#
# Files are loaded in parallel: each one reads one source and writes one
# output, so the workers never touch the same file.
#
# Run from the project root:  .venv/bin/python pipeline/load.py

import csv
import multiprocessing
import os

import openpyxl

INVENTORY = "pipeline/inventory.csv"
OUTPUT_DIR = "data/clean"
WORKERS = 8

# Column names that mean the same thing but were spelled differently over the
# years. Every other name is simply lowercased, which already fixes NB_Fer,
# ID_ZdC and similar casing changes.
ALIASES = {
    "date": "jour",
    "lda": "zone_id",
    "id_refa_lda": "zone_id",
    "id_zdc": "zone_id",
    "pourcentage_validations": "pourc_validations",
}

# Timetable and map geometry: 14.6M rows we have no question for. Remove these
# two lines to include them.
SKIP = ["stop_times.txt", "shapes.txt"]

SEPARATORS = {"semicolon": ";", "tab": "\t", "comma": ","}


def clean_name(name, dataset):
    """Lowercase a column name and apply the alias map.

    The aliases only apply to the validation files. GTFS has its own column
    called date, meaning a service exception date, which is a different thing
    from a validation day and must keep its own name.
    """
    name = name.strip().lower()
    if dataset in ("reseau-ferre", "reseau-surface") and name in ALIASES:
        return ALIASES[name]
    return name


def clean_date(value):
    """Turn a date into YYYY-MM-DD, whatever shape it arrived in.

    Four shapes appear across the sources:
        01/07/2024    most validation files
        01/07/24      2024_T3_NB_FER and 2024_T4_NB_SURFACE
        2024-10-01    2024_T4_NB_FER, and every Excel file
        20241001      GTFS

    Anything else stops the run. An earlier version returned unrecognised
    values unchanged, which let 466,121 two-digit-year dates through to the
    database, where they were read as the years 2001 to 2031. Half of those
    landed inside 2015-2025 and so looked perfectly valid.
    """
    value = str(value).strip()

    if len(value) == 10 and value[2] == "/" and value[5] == "/":
        return value[6:10] + "-" + value[3:5] + "-" + value[0:2]

    if len(value) == 8 and value[2] == "/" and value[5] == "/":
        return "20" + value[6:8] + "-" + value[3:5] + "-" + value[0:2]

    if len(value) >= 10 and value[4] == "-" and value[7] == "-":
        return value[0:10]

    if len(value) == 8 and value.isdigit():
        return value[0:4] + "-" + value[4:6] + "-" + value[6:8]

    raise ValueError("unrecognised date format: " + repr(value))


def load_one(item):
    """Read one source file and write one clean copy.

    This runs in a worker process. It returns a short result line rather than
    printing, so the output does not interleave between workers.
    """
    path = item["path"]
    name = item["filename"]

    # --- open the file using the format the profiler recorded ---
    is_excel = item["encoding"] == "excel"

    if is_excel:
        book = openpyxl.load_workbook(path, read_only=True)
        sheet = book.active
        reader = sheet.rows
    else:
        separator = SEPARATORS[item["separator"]]
        file = open(path, encoding=item["encoding"], errors="strict")
        reader = csv.reader(file, delimiter=separator)

    # --- work out the new column names from the old ones ---
    old_columns = item["columns"].split("|")

    columns = []
    zone_id_source = ""

    for old in old_columns:
        new = clean_name(old, item["dataset"])
        if new == "zone_id":
            # remember which spelling this file actually used
            zone_id_source = old.strip().lower()
        columns.append(new)

    uses_decimal_comma = item["decimal"] == ","

    # --- prepare the output file ---
    out_folder = OUTPUT_DIR + "/" + item["dataset"]
    os.makedirs(out_folder, exist_ok=True)

    out_path = out_folder + "/" + os.path.splitext(name)[0] + ".csv"
    out_file = open(out_path, "w", newline="", encoding="utf-8")
    writer = csv.writer(out_file)

    header = list(columns)
    if zone_id_source:
        header.append("zone_id_source")
    header.append("source_file")
    writer.writerow(header)

    # --- read and write one row at a time, so memory stays flat ---
    n_written = 0
    first = True

    for row in reader:
        if is_excel:
            values = []
            for cell in row:
                values.append(cell.value)
        else:
            values = row

        if first:
            # this is the header line, which we have already handled
            first = False
            continue

        clean_row = []
        for i in range(len(values)):
            value = values[i]
            if value is None:
                value = ""
            value = str(value).strip()

            if i < len(columns):
                if columns[i] == "jour":
                    value = clean_date(value)
                elif uses_decimal_comma and columns[i] == "pourc_validations":
                    value = value.replace(",", ".")
                elif columns[i] == "nb_vald":
                    # 2024_T3_NB_FER writes 2093 as "2 093", using a space as
                    # the thousands separator. Only strip the spaces when what
                    # remains is a number, so "Moins de 5" is left alone: that
                    # is a suppressed count, not a formatting problem, and it
                    # must stay visible rather than be quietly turned into one.
                    compact = value.replace(" ", "")
                    if compact.isdigit():
                        value = compact

            clean_row.append(value)

        if zone_id_source:
            clean_row.append(zone_id_source)
        clean_row.append(name)

        writer.writerow(clean_row)
        n_written = n_written + 1

    out_file.close()
    if is_excel:
        book.close()
    else:
        file.close()

    expected = int(item["n_rows"])
    flag = "ok"
    if n_written != expected:
        flag = "MISMATCH expected " + str(expected)

    return name + "  " + str(n_written) + " rows  " + flag


# Everything below runs only in the main process. Without this guard each
# worker would import this file and run it again, starting more workers.
if __name__ == "__main__":
    inventory = list(csv.DictReader(open(INVENTORY, encoding="utf-8")))

    jobs = []
    for item in inventory:
        if item["filename"] in SKIP:
            print("skipping " + item["filename"])
        else:
            jobs.append(item)

    done = 0
    pool = multiprocessing.Pool(WORKERS)

    for result in pool.imap_unordered(load_one, jobs):
        done = done + 1
        print("[" + str(done) + "/" + str(len(jobs)) + "] " + result)

    pool.close()
    pool.join()

    print("Done")
