import zipfile
import os

files = os.listdir("reseau-surface")

for name in files:
    if name.endswith(".zip"):
        print("Unzipping " + name)
        zip_file = zipfile.ZipFile("reseau-surface/" + name)
        folder = "reseau-surface/" + name.replace(".zip", "")
        zip_file.extractall(folder)
        zip_file.close()

print("Done!")
