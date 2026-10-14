import csv
import urllib.request
import os

os.makedirs("reseau-surface", exist_ok=True)

file = open("histo-validations-reseau-surface.csv", encoding="utf-8-sig")
reader = csv.reader(file, delimiter=";")
next(reader)  # skip header row

for row in reader:
    year = row[0]
    url = row[1]
    print("Downloading " + year)
    urllib.request.urlretrieve(url, "reseau-surface/data-surface-" + year + ".zip")

file.close()
print("Done!")
