import zipfile

zip_file = zipfile.ZipFile("IDFM-gtfs.zip")
zip_file.extractall("gtfs")
zip_file.close()

print("Done!")
