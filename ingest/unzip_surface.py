# Run from the project root:  .venv/bin/python ingest/unzip_surface.py

import zipfile
import os

files = os.listdir("data/raw/reseau-surface")

for name in files:
    if name.endswith(".zip"):
        print("Unzipping " + name)
        zip_file = zipfile.ZipFile("data/raw/reseau-surface/" + name)
        folder = "data/raw/reseau-surface/" + name.replace(".zip", "")
        zip_file.extractall(folder)
        zip_file.close()

print("Done!")
