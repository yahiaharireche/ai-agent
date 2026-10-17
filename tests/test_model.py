# Automatic tests for the final database, data/model/ridership.duckdb.
#
# The build scripts already stop on many problems. These tests are separate on
# purpose: they re-check the finished database from the outside, the way the AI
# will read it, so a change to any build script that quietly breaks the data
# makes a test fail.
#
# Every test only reads. Run from the project root:
#   .venv/bin/python -m pytest tests

import duckdb
import pytest

DATABASE = "data/model/ridership.duckdb"
PARQUET = "data/parquet"

# Metro and train taps per year, measured when the database was first built and
# checked against the results from earlier phases. A change here means either
# the data changed, or a build step broke something.
RAIL_TAPS_PER_YEAR = {
    2015: 1493460776,
    2016: 1589563064,
    2017: 1671982464,
    2018: 1675803004,
    2019: 1695721547,
    2020: 926003724,
    2021: 1251011175,
    2022: 1628679804,
    2023: 1726555186,
    2024: 1905998830,
    2025: 2157659651,
}


@pytest.fixture(scope="module")
def db():
    """One read-only connection, shared by all the tests in this file."""
    connection = duckdb.connect(DATABASE, read_only=True)
    yield connection
    connection.close()


def one(db, sql):
    """Run a query that returns a single value, and return it."""
    return db.sql(sql).fetchone()[0]


# --- nothing lost or duplicated between the clean data and the final tables ---

@pytest.mark.parametrize("fact, parquet", [
    ("fact_rail_daily", "rail_counts"),
    ("fact_surface_daily", "surface_counts"),
])
def test_daily_rows_and_taps_match_clean_data(db, fact, parquet):
    rows_in, taps_in = db.sql("SELECT COUNT(*), SUM(TRY_CAST(nb_vald AS BIGINT)) FROM '" +
                              PARQUET + "/" + parquet + ".parquet'").fetchone()
    rows_out, taps_out = db.sql("SELECT COUNT(*), SUM(taps) FROM " + fact).fetchone()
    assert rows_out == rows_in
    assert taps_out == taps_in


@pytest.mark.parametrize("fact, parquet", [
    ("fact_rail_hourly", "rail_hourly"),
    ("fact_surface_hourly", "surface_hourly"),
])
def test_hourly_rows_match_clean_data(db, fact, parquet):
    rows_in = one(db, "SELECT COUNT(*) FROM '" + PARQUET + "/" + parquet + ".parquet'")
    assert one(db, "SELECT COUNT(*) FROM " + fact) == rows_in


# --- the links between tables hold ---

@pytest.mark.parametrize("table, key", [
    ("dim_station", "station_key"),
    ("dim_line", "line_key"),
    ("dim_fare_category", "label"),
])
def test_dimension_keys_are_unique_and_present(db, table, key):
    rows, distinct, missing = db.sql(
        "SELECT COUNT(*), COUNT(DISTINCT " + key + "), COUNT(*) FILTER (WHERE " + key +
        " IS NULL) FROM " + table).fetchone()
    assert missing == 0
    assert distinct == rows


@pytest.mark.parametrize("fact, key, dimension, dimension_key", [
    ("fact_rail_daily", "station_key", "dim_station", "station_key"),
    ("fact_rail_hourly", "station_key", "dim_station", "station_key"),
    ("fact_surface_daily", "line_key", "dim_line", "line_key"),
    ("fact_surface_hourly", "line_key", "dim_line", "line_key"),
    ("fact_rail_daily", "ticket_label", "dim_fare_category", "label"),
    ("fact_surface_daily", "ticket_label", "dim_fare_category", "label"),
])
def test_every_fact_key_exists_in_its_dimension(db, fact, key, dimension, dimension_key):
    # The database enforces these as foreign keys; this checks it independently,
    # in case the keys are ever dropped from the build.
    orphans = one(db, "SELECT COUNT(*) FROM " + fact + " f LEFT JOIN " + dimension +
                  " d ON f." + key + " = d." + dimension_key +
                  " WHERE f." + key + " IS NOT NULL AND d." + dimension_key + " IS NULL")
    assert orphans == 0


@pytest.mark.parametrize("fact, key", [
    ("fact_rail_daily", "station_key"),
    ("fact_rail_hourly", "station_key"),
    ("fact_surface_daily", "line_key"),
    ("fact_surface_hourly", "line_key"),
])
def test_every_fact_row_has_a_key(db, fact, key):
    assert one(db, "SELECT COUNT(*) FROM " + fact + " WHERE " + key + " IS NULL") == 0


# --- the database explains itself ---

def test_every_table_has_a_description(db):
    # "NOT internal" leaves out DuckDB's own built-in tables
    undescribed = db.sql("SELECT table_name FROM duckdb_tables()"
                         " WHERE NOT internal AND (comment IS NULL OR comment = '')").fetchall()
    assert undescribed == []


def test_every_column_has_a_description(db):
    undescribed = db.sql("SELECT table_name || '.' || column_name FROM duckdb_columns()"
                         " WHERE NOT internal AND (comment IS NULL OR comment = '')").fetchall()
    assert undescribed == []


# --- warnings ---

def test_every_warning_covers_rows(db):
    empty = db.sql("SELECT flag_id FROM quality_flags WHERE rows_affected <= 0").fetchall()
    assert empty == []


def test_hidden_count_warnings_apply_to_2016(db):
    flags = [row[0] for row in db.sql(
        "SELECT flag_id FROM warnings_between('2016-01-01', '2016-12-31')").fetchall()]
    assert "suppressed_counts_rail" in flags
    assert "suppressed_counts_surface" in flags


def test_hidden_count_warnings_do_not_apply_to_2019(db):
    flags = [row[0] for row in db.sql(
        "SELECT flag_id FROM warnings_between('2019-01-01', '2019-12-31')").fetchall()]
    assert "suppressed_counts_rail" not in flags
    assert "suppressed_counts_surface" not in flags


# --- known answers and honest values ---

def test_rail_taps_per_year_match_known_answers(db):
    got = dict(db.sql("SELECT year, SUM(taps) FROM fact_rail_daily"
                      " GROUP BY year ORDER BY year").fetchall())
    assert got == RAIL_TAPS_PER_YEAR


@pytest.mark.parametrize("fact", ["fact_rail_daily", "fact_surface_daily"])
def test_hidden_counts_are_empty_not_zero(db, fact):
    # A count IDFM hid ("Moins de 5") is unknown. Turning it into 0 would invent
    # data and make totals look exact when they are not.
    hidden, hidden_with_a_number = db.sql(
        "SELECT COUNT(*) FILTER (WHERE was_hidden),"
        " COUNT(*) FILTER (WHERE was_hidden AND taps IS NOT NULL) FROM " + fact).fetchone()
    assert hidden > 0
    assert hidden_with_a_number == 0


@pytest.mark.parametrize("fact", ["fact_rail_daily", "fact_surface_daily"])
def test_daily_dates_are_inside_2015_to_2025(db, fact):
    first, last, missing = db.sql("SELECT MIN(date), MAX(date), COUNT(*) FILTER"
                                  " (WHERE date IS NULL) FROM " + fact).fetchone()
    assert missing == 0
    assert str(first) >= "2015-01-01"
    assert str(last) <= "2025-12-31"


@pytest.mark.parametrize("fact", ["fact_rail_hourly", "fact_surface_hourly"])
def test_hourly_periods_are_inside_2015_to_2025(db, fact):
    first, last = db.sql("SELECT MIN(period_start), MAX(period_end) FROM " + fact).fetchone()
    assert str(first) >= "2015-01-01"
    assert str(last) <= "2025-12-31"


@pytest.mark.parametrize("fact", ["fact_rail_hourly", "fact_surface_hourly"])
def test_usable_profiles_add_up_to_100(db, fact):
    wrong = one(db, "SELECT COUNT(*) FROM " + fact +
                " WHERE profile_ok AND profile_total_pct NOT BETWEEN 99 AND 101")
    assert wrong == 0
