import urllib.request

url = "https://data.iledefrance.fr/api/explore/v2.1/catalog/datasets/offre-horaires-tc-gtfs-idfm/files/a925e164271e4bca93433756d6a340d1"

print("Downloading GTFS...")
urllib.request.urlretrieve(url, "IDFM-gtfs.zip")
print("Done!")
