# Run from the project root:  .venv/bin/python ingest/download_surface.py

import csv
import urllib.request
import os

os.makedirs("data/raw/reseau-surface", exist_ok=True)

file = open("ingest/histo-validations-reseau-surface.csv", encoding="utf-8-sig")
reader = csv.reader(file, delimiter=";")
next(reader)  # skip header row

for row in reader:
    year = row[0]
    url = row[1]
    print("Downloading " + year)
    urllib.request.urlretrieve(url, "data/raw/reseau-surface/data-surface-" + year + ".zip")

file.close()
print("Done!")
