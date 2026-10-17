# Run from the project root:  .venv/bin/python ingest/unzip_gtfs.py

import zipfile

zip_file = zipfile.ZipFile("data/raw/IDFM-gtfs.zip")
zip_file.extractall("data/raw/gtfs")
zip_file.close()

print("Done!")
