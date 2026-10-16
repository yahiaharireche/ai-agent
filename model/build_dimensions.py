# Builds the dimension tables: what each identifier in the validation data
# actually refers to. Reads data/parquet/, writes data/model/.
#
#   dim_station        rail: which real station each (zone_id, station code) is
#   dim_line           surface: which real line each id_groupoflines is
#   dim_fare_category  every fare label, mapped across the July 2023 relabelling
#
# Every row records HOW it was matched, so an answer built on it can say
# whether a name came from an exact ID match or from a weaker inference.
#
# Run from the project root:  .venv/bin/python model/build_dimensions.py

import os

import duckdb

PARQUET = "data/parquet"
OUTPUT = "data/model"

db = duckdb.connect()
os.makedirs(OUTPUT, exist_ok=True)

db.sql("CREATE VIEW rail AS SELECT * FROM '" + PARQUET + "/rail_counts.parquet'")
db.sql("CREATE VIEW surface AS SELECT * FROM '" + PARQUET + "/surface_counts.parquet'")

# GTFS parent stations. Their ids are "IDFM:" followed by the same zone id the
# validation files use - checked: 98.2% of validations match directly.
db.sql("""
    CREATE VIEW gtfs_station AS
    SELECT replace(stop_id, 'IDFM:', '') AS id, stop_name
    FROM '""" + PARQUET + """/gtfs/stops.parquet'
    WHERE location_type = '1'
""")


# --- dim_station -------------------------------------------------------------
#
# Keyed on (zone_id, station code), not zone_id alone: a placeholder id such as
# '?' or '-1' was used for several different stations, and only the station
# code tells them apart. NULLs become '' so the key can be joined on.
#
# Match methods, strongest first:
#   exact_id          the zone_id is a current GTFS station
#   via_station_code  it is not, but the same station code later carries a zone_id
#                     that is - e.g. Montparnasse, whose id changed in April 2025
#   unknown_station   IDFM recorded the station as unknown (code ND / INCONNU)
#   unmatched         none of the above; listed, never silently dropped

db.sql("""
    CREATE TABLE seen AS
    SELECT coalesce(zone_id, '') AS zone_id,
           coalesce(code_stif_arret, '') AS code_stif_arret,
           mode(upper(trim(libelle_arret))) AS validation_name,
           MIN(jour) AS first_seen,
           MAX(jour) AS last_seen,
           SUM(TRY_CAST(nb_vald AS BIGINT)) AS validations
    FROM rail
    GROUP BY 1, 2
""")

db.sql("""
    CREATE TABLE dim_station AS
    WITH via_code AS (
        -- for each station code, the most recently used zone_id that is a
        -- current GTFS station
        SELECT s.code_stif_arret, arg_max(s.zone_id, s.last_seen) AS station_id
        FROM seen s JOIN gtfs_station g ON s.zone_id = g.id
        WHERE s.code_stif_arret NOT IN ('', 'ND')
        GROUP BY 1
    )
    SELECT s.zone_id,
           s.code_stif_arret,
           CASE WHEN direct.id IS NOT NULL THEN direct.id
                WHEN v.station_id IS NOT NULL THEN v.station_id END AS station_id,
           coalesce(direct.stop_name, via.stop_name) AS station_name,
           CASE WHEN direct.id IS NOT NULL THEN 'exact_id'
                WHEN s.validation_name = 'INCONNU' OR s.code_stif_arret = 'ND'
                     THEN 'unknown_station'
                WHEN v.station_id IS NOT NULL THEN 'via_station_code'
                ELSE 'unmatched' END AS match_method,
           s.validation_name,
           s.first_seen,
           s.last_seen,
           s.validations
    FROM seen s
    LEFT JOIN gtfs_station direct ON s.zone_id = direct.id
    LEFT JOIN via_code v ON s.code_stif_arret = v.code_stif_arret
    LEFT JOIN gtfs_station via ON v.station_id = via.id
""")


# --- dim_line ----------------------------------------------------------------
#
# Surface validations are counted per line group. id_groupoflines matches the
# line reference; code_stif_ligne matches nothing there (checked: 0 of 726).

db.sql("""
    CREATE TABLE dim_line AS
    WITH ref AS (
        SELECT id_groupoflines,
               any_value(shortname_groupoflines) AS line_name,
               string_agg(DISTINCT transportmode, ', ') AS transport_mode,
               string_agg(DISTINCT operatorname, ', ') AS operator
        FROM '""" + PARQUET + """/extra/referentiel-des-lignes.parquet'
        GROUP BY 1
    ),
    seen AS (
        SELECT coalesce(id_groupoflines, '') AS id_groupoflines,
               mode(upper(trim(libelle_ligne))) AS validation_name,
               MIN(jour) AS first_seen,
               MAX(jour) AS last_seen,
               SUM(TRY_CAST(nb_vald AS BIGINT)) AS validations
        FROM surface
        GROUP BY 1
    )
    SELECT s.id_groupoflines,
           r.line_name,
           r.transport_mode,
           r.operator,
           CASE WHEN r.id_groupoflines IS NOT NULL THEN 'exact_id'
                -- IDFM's own "line not known" markers: '?' until mid-2017,
                -- A00000 from mid-2022. 916M validations sit under '?' alone.
                WHEN s.id_groupoflines IN ('?', 'A00000') THEN 'unknown_line'
                ELSE 'unmatched' END AS match_method,
           s.validation_name,
           s.first_seen,
           s.last_seen,
           s.validations
    FROM seen s
    LEFT JOIN ref r ON s.id_groupoflines = r.id_groupoflines
""")


# --- dim_fare_category -------------------------------------------------------
#
# The mapping is a judgement, so it lives in model/fare_categories.csv where it
# can be read and reviewed, with the evidence for each line. Every label that
# appears in the data must be in it: an unmapped label stops the build.

db.sql("""
    CREATE TABLE dim_fare_category AS
    SELECT * FROM read_csv('model/fare_categories.csv', header=true, all_varchar=true)
""")

unmapped = db.sql("""
    SELECT DISTINCT label FROM (
        SELECT categorie_titre AS label FROM rail
        UNION SELECT categorie_titre FROM surface)
    WHERE label NOT IN (SELECT label FROM dim_fare_category)
    ORDER BY 1
""").fetchall()

if unmapped:
    labels = []
    for row in unmapped:
        labels.append(repr(row[0]))
    raise SystemExit("fare labels missing from model/fare_categories.csv: " +
                     ", ".join(labels))


# --- write and report --------------------------------------------------------

for table in ["dim_station", "dim_line", "dim_fare_category"]:
    db.sql("COPY " + table + " TO '" + OUTPUT + "/" + table + ".parquet' (FORMAT PARQUET)")
    n = db.sql("SELECT COUNT(*) FROM " + table).fetchone()[0]
    print("wrote " + OUTPUT + "/" + table + ".parquet  (" + str(n) + " rows)")

for table, label in [("dim_station", "rail stations"), ("dim_line", "surface lines")]:
    print("\ncoverage, " + label + " - share of validations by how they were matched:")
    rows = db.sql("""
        SELECT match_method, COUNT(*),
               ROUND(100.0 * SUM(validations) / SUM(SUM(validations)) OVER (), 3)
        FROM """ + table + " GROUP BY 1 ORDER BY 3 DESC").fetchall()
    for method, n, share in rows:
        print("  %-18s %5d ids  %8s%%" % (method, n, share))
