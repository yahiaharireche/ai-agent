# Builds the final tables the AI will query, in one database file:
# data/model/ridership.duckdb
#
# Each fact table takes the clean tap counts and puts everything needed to
# answer a question on the same row: real dates, the station or line name,
# the ticket category, and the count as a real number. The tricky matching
# was already done in build_dimensions.py; here it is only joined on.
#
# The links between tables are declared as keys, so the database itself knows
# them and enforces them:
#   - each meaning table has a primary key (station_key, line_key, label)
#   - each fact table stores that key, declared as a foreign key, so a fact row
#     pointing to a station, line or ticket type that does not exist is refused
#
# Enforcing the keys has a cost: DuckDB keeps an index to check them, which
# makes loading about 80 times slower (rail daily: 0.6s -> 48s) and the file
# about 6 times larger for those columns. That was judged worth it: the whole
# build stays at a few minutes, and the links can no longer silently break.
#
# Three rules, all checked before the file is kept:
#   - no row is lost or duplicated: rows out == rows in, for every table
#   - no tap is lost or duplicated: total taps out == total taps in
#   - nothing is invented: a hidden count ("Moins de 5") stays empty, with
#     was_hidden = true, rather than becoming 0
#
# Run from the project root, after build_dimensions.py:
#   .venv/bin/python model/build_facts.py

import os

import duckdb

PARQUET = "data/parquet"
MODEL = "data/model"
DATABASE = MODEL + "/ridership.duckdb"

# Start from an empty file every time, so a rerun gives the same result.
if os.path.exists(DATABASE):
    os.remove(DATABASE)

db = duckdb.connect(DATABASE)
problems = []


def check(label, got, expected):
    """Record a mismatch instead of stopping, so every problem is reported."""
    status = "ok" if got == expected else "MISMATCH"
    print("  %-34s %18s  (expected %s)  %s" %
          (label, format(got, ","), format(expected, ","), status))
    if got != expected:
        problems.append(label)


def create_table(name, select, primary_key=None, foreign_keys=()):
    """Create a table with its keys declared, then fill it from a query.

    DuckDB only accepts a foreign key when a table is created, not added
    afterwards, and CREATE TABLE ... AS SELECT cannot declare one. So the
    columns and their types are read from the query first, the table is
    created empty with its keys, and then the rows are inserted.
    """
    columns = db.sql("DESCRIBE SELECT * FROM (" + select + ") LIMIT 0").fetchall()

    parts = []
    for column in columns:
        parts.append('"' + column[0] + '" ' + column[1])
    if primary_key:
        parts.append("PRIMARY KEY (" + primary_key + ")")
    for column, other_table, other_column in foreign_keys:
        parts.append("FOREIGN KEY (" + column + ") REFERENCES " +
                     other_table + " (" + other_column + ")")

    db.sql("CREATE TABLE " + name + " (" + ", ".join(parts) + ")")
    db.sql("INSERT INTO " + name + " SELECT * FROM (" + select + ")")


# --- the meaning tables, with their primary keys -----------------------------

create_table("dim_station", "SELECT * FROM '" + MODEL + "/dim_station.parquet'",
             primary_key="station_key")
create_table("dim_line", "SELECT * FROM '" + MODEL + "/dim_line.parquet'",
             primary_key="line_key")
create_table("dim_fare_category", "SELECT * FROM '" + MODEL + "/dim_fare_category.parquet'",
             primary_key="label")


# --- 5.1  daily metro and train taps -----------------------------------------
#
# The station key is looked up here, from the two raw columns. This is the only
# place the coalesce(..., '') trick is needed: the station table stores empty
# values as '', the raw data as empty. After this, queries join on station_key.

print("fact_rail_daily")
create_table("fact_rail_daily", """
    SELECT CAST(r.jour AS DATE)                 AS date,
           year(CAST(r.jour AS DATE))           AS year,
           month(CAST(r.jour AS DATE))          AS month,
           d.station_key,
           d.station_id,
           d.station_name,
           d.match_method                       AS station_match,
           r.zone_id                            AS station_id_in_source,
           r.code_stif_arret                    AS station_code_in_source,
           r.libelle_arret                      AS station_name_in_source,
           r.code_stif_trns                     AS operator_code,
           r.categorie_titre                    AS ticket_label,
           f.category                           AS ticket_category,
           f.mapping                            AS ticket_category_mapping,
           TRY_CAST(r.nb_vald AS BIGINT)        AS taps,
           r.nb_vald = 'Moins de 5'             AS was_hidden,
           r.source_file
    FROM '""" + PARQUET + """/rail_counts.parquet' r
    LEFT JOIN dim_station d
           ON coalesce(r.zone_id, '') = d.zone_id
          AND coalesce(r.code_stif_arret, '') = d.code_stif_arret
    LEFT JOIN dim_fare_category f ON r.categorie_titre = f.label
""", foreign_keys=[("station_key", "dim_station", "station_key"),
                   ("ticket_label", "dim_fare_category", "label")])

rows_in, taps_in = db.sql("SELECT COUNT(*), SUM(TRY_CAST(nb_vald AS BIGINT)) FROM '" +
                          PARQUET + "/rail_counts.parquet'").fetchone()
rows_out, taps_out, no_key = db.sql(
    "SELECT COUNT(*), SUM(taps), COUNT(*) FILTER (WHERE station_key IS NULL)"
    " FROM fact_rail_daily").fetchone()
check("rows", rows_out, rows_in)
check("total taps", taps_out, taps_in)
check("rows with no station_key", no_key, 0)


# --- 5.2  daily bus and tram taps --------------------------------------------

print("fact_surface_daily")
create_table("fact_surface_daily", """
    SELECT CAST(s.jour AS DATE)                 AS date,
           year(CAST(s.jour AS DATE))           AS year,
           month(CAST(s.jour AS DATE))          AS month,
           l.line_key,
           s.id_groupoflines                    AS line_id,
           l.line_name,
           l.transport_mode,
           l.operator,
           l.match_method                       AS line_match,
           s.libelle_ligne                      AS line_name_in_source,
           s.code_stif_ligne                    AS line_code_in_source,
           s.code_stif_trns                     AS operator_code,
           s.categorie_titre                    AS ticket_label,
           f.category                           AS ticket_category,
           f.mapping                            AS ticket_category_mapping,
           TRY_CAST(s.nb_vald AS BIGINT)        AS taps,
           s.nb_vald = 'Moins de 5'             AS was_hidden,
           s.source_file
    FROM '""" + PARQUET + """/surface_counts.parquet' s
    LEFT JOIN dim_line l ON coalesce(s.id_groupoflines, '') = l.id_groupoflines
    LEFT JOIN dim_fare_category f ON s.categorie_titre = f.label
""", foreign_keys=[("line_key", "dim_line", "line_key"),
                   ("ticket_label", "dim_fare_category", "label")])

rows_in, taps_in = db.sql("SELECT COUNT(*), SUM(TRY_CAST(nb_vald AS BIGINT)) FROM '" +
                          PARQUET + "/surface_counts.parquet'").fetchone()
rows_out, taps_out, no_key = db.sql(
    "SELECT COUNT(*), SUM(taps), COUNT(*) FILTER (WHERE line_key IS NULL)"
    " FROM fact_surface_daily").fetchone()
check("rows", rows_out, rows_in)
check("total taps", taps_out, taps_in)
check("rows with no line_key", no_key, 0)


# --- 5.3  hourly profiles ----------------------------------------------------
#
# These hold the share of a typical day's taps in each hour, per station or
# line, per type of day, per half-year or quarter. Three things need doing:
#
#   - they have no date: the period is only in the file name, e.g.
#     2015S1 = Jan-Jun 2015, 2024_T3 = Jul-Sep 2024
#   - the type of day is a French code, translated below
#   - hours are text like "7H-8H", which sorts wrongly ("10H" before "1H"),
#     so they become a number; "ND" (not defined) becomes empty
#
# A profile should add up to 100%. 99.8% of rail and 97.7% of bus and tram
# profiles do. The rest either stack several profiles under one key (an hour
# listed more than once) or are incomplete. Neither can be repaired, so every
# row is kept and profile_ok says whether its profile is usable.
#
# Missing hours mean zero taps (the station was closed), not unknown.

PERIOD = """
    make_date(CAST(substr(source_file, 1, 4) AS INTEGER),
              CASE regexp_extract(source_file, '(S[12]|T[1-4])')
                   WHEN 'S1' THEN 1 WHEN 'S2' THEN 7
                   WHEN 'T1' THEN 1 WHEN 'T2' THEN 4
                   WHEN 'T3' THEN 7 WHEN 'T4' THEN 10 END, 1)
"""

PERIOD_MONTHS = """
    CASE WHEN regexp_extract(source_file, '(S[12]|T[1-4])') LIKE 'S%' THEN 6 ELSE 3 END
"""

DAY_TYPE = """
    CASE cat_jour
         WHEN 'JOHV'  THEN 'weekday, outside school holidays'
         WHEN 'JOVS'  THEN 'weekday, during school holidays'
         WHEN 'SAHV'  THEN 'Saturday, outside school holidays'
         WHEN 'SAVS'  THEN 'Saturday, during school holidays'
         WHEN 'DIJFP' THEN 'Sunday or public holiday'
    END
"""

HOUR = """
    CASE WHEN trnc_horr_60 = 'ND' THEN NULL
         ELSE CAST(regexp_extract(trnc_horr_60, '^([0-9]+)H', 1) AS INTEGER) END
"""

for table, source, key, extra_join, extra_cols, key_column, foreign_key in [
    ("fact_rail_hourly", "rail_hourly",
     "coalesce(h.zone_id, '') || '|' || coalesce(h.code_stif_arret, '')",
     """LEFT JOIN dim_station d ON coalesce(h.zone_id, '') = d.zone_id
                                 AND coalesce(h.code_stif_arret, '') = d.code_stif_arret""",
     """d.station_key, d.station_id, d.station_name, d.match_method AS station_match,
        h.zone_id AS station_id_in_source,
        h.code_stif_arret AS station_code_in_source,
        h.libelle_arret AS station_name_in_source,""",
     "station_key", ("station_key", "dim_station", "station_key")),
    ("fact_surface_hourly", "surface_hourly",
     "coalesce(h.id_groupoflines, '') || '|' || coalesce(h.code_stif_ligne, '')",
     "LEFT JOIN dim_line l ON coalesce(h.id_groupoflines, '') = l.id_groupoflines",
     """l.line_key, h.id_groupoflines AS line_id, l.line_name, l.transport_mode,
        l.match_method AS line_match,
        h.libelle_ligne AS line_name_in_source,
        h.code_stif_ligne AS line_code_in_source,""",
     "line_key", ("line_key", "dim_line", "line_key")),
]:
    print(table)
    create_table(table, """
        WITH h AS (
            SELECT *, """ + PERIOD + """ AS period_start,
                   """ + PERIOD_MONTHS + """ AS period_months
            FROM '""" + PARQUET + "/" + source + """.parquet'
        ),
        rows AS (
            SELECT h.period_start,
                   CAST(h.period_start + to_months(h.period_months)
                        - INTERVAL 1 DAY AS DATE)            AS period_end,
                   year(h.period_start)                       AS year,
                   """ + extra_cols + """
                   h.cat_jour                                 AS day_type_code,
                   """ + DAY_TYPE.replace("cat_jour", "h.cat_jour") + """ AS day_type,
                   """ + HOUR.replace("trnc_horr_60", "h.trnc_horr_60") + """ AS hour,
                   TRY_CAST(h.pourc_validations AS DOUBLE)    AS share_pct,
                   h.source_file,
                   -- what identifies one profile, for the consistency check
                   h.source_file || '|' || """ + key + """
                       || '|' || h.cat_jour                   AS profile_key,
                   h.trnc_horr_60                             AS hour_slot
            FROM h """ + extra_join + """
        )
        -- Totals are rounded: adding decimals in a different order gives
        -- 98.999999 one run and 99.000001 the next, and 92 bus and tram rows sit
        -- exactly on the 99% line, so unrounded they flip between rebuilds.
        SELECT * EXCLUDE (profile_key, hour_slot),
               ROUND(SUM(share_pct) OVER (PARTITION BY profile_key), 6)   AS profile_total_pct,
               ROUND(SUM(share_pct) OVER (PARTITION BY profile_key), 6) BETWEEN 99 AND 101
               AND COUNT(*) OVER (PARTITION BY profile_key)
                   = COUNT(DISTINCT hour_slot) OVER (PARTITION BY profile_key)
                                                                          AS profile_ok
        FROM rows
    """, foreign_keys=[foreign_key])

    rows_in = db.sql("SELECT COUNT(*) FROM '" + PARQUET + "/" + source +
                     ".parquet'").fetchone()[0]
    rows_out, no_period, no_day_type, no_key, bad = db.sql(
        "SELECT COUNT(*), COUNT(*) FILTER (WHERE period_start IS NULL),"
        " COUNT(*) FILTER (WHERE day_type IS NULL),"
        " COUNT(*) FILTER (WHERE " + key_column + " IS NULL),"
        " COUNT(*) FILTER (WHERE NOT profile_ok) FROM " + table).fetchone()
    check("rows", rows_out, rows_in)
    check("rows with no period", no_period, 0)
    check("rows with unknown day type", no_day_type, 0)
    check("rows with no " + key_column, no_key, 0)
    print("  %-34s %18s  (kept, marked profile_ok = false)" %
          ("rows in inconsistent profiles", format(bad, ",")))


if problems:
    db.close()
    os.remove(DATABASE)
    raise SystemExit("build failed, database removed: " + ", ".join(problems))

db.close()
print("\nwrote " + DATABASE)
