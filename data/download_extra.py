import urllib.request
import gzip
import os

os.makedirs("extra", exist_ok=True)

datasets = [
    "arrets",
    "referentiel-des-lignes",
    "arrets-lignes",
    "indicateurs-qualite-service-sncf-ratp",
    "indicateurs-qualite-service-parcours-voyageur",
    "titres-et-tarifs",
    "offre_hebdomadaire_moyenne_hors_vacances",
    "offre_hebdomadaire_moyenne_vacances_ete",
    "offre_hebdomadaire_moyenne_vacances_scolaires",
]

base_url = "https://data.iledefrance-mobilites.fr/api/explore/v2.1/catalog/datasets/"

for name in datasets:
    print("Downloading " + name + "...")
    url = base_url + name + "/exports/csv"
    data = urllib.request.urlopen(url).read()

    # some datasets come back gzip-compressed even without asking
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)

    out_path = "extra/" + name + ".csv"
    f = open(out_path, "wb")
    f.write(data)
    f.close()

print("Done!")
