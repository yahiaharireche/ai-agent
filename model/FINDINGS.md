# Modelling decisions

Each decision below was made from measurement on the data, not assumed. The
numbers can be reproduced from `data/parquet/` and `data/model/`.

## 1. Rail can be summed across the July 2023 identifier change

The station identifier column was renamed `ID_REFA_LDA` → `lda` → `ID_ZDC`
in 2022–2023, and rail rows per half-year fell 21% at the same moment. The
hypothesis was that the new IDs group stations differently. **They do not.**

| Evidence | Result |
|---|---|
| Distinct IDs per era | 760 (LDA) vs 769 (ZDC) |
| IDs used under both column names | 743 |
| Stations with one ID in each era, same ID | 696 of 698 |
| Monthly totals, July–Oct 2023 vs 2022 | +3% to +8%, no break |

The rename changed the column, not the values.

**The row drop came from fare categories**, which went from 9 labels to 7 on
2023-07-01. Stations went up (756 → 769); rows per station per day fell
(8.0 → 6.0).

## 2. Fare categories map across 2023 with three levels of confidence

Matched by share of validations either side of the change, not by name.
Recorded in `fare_categories.csv` with the evidence for each line.

- **exact:** Navigo, Imagine R, Amethyste, Non défini
- **merged:** TST + FGT → Contrat Solidarité Transport
- **approximate:** Autres titres, Forfaits courts
- **no successor:** `?` (2.3% of validations)

Totals are comparable across the change; per-category trends are only as
good as the mapping.

## 3. Station and line identity comes from IDs, never names

Station codes carry inconsistent names: 419 of 806 have more than one
spelling (`ST-GERM.D.PRES` / `SAINT-GERMAIN-DES-PRES`). Names are not used
for matching.

**Rail** (`dim_station`): `zone_id` is the GTFS parent-station id.

| Match | Share of validations |
|---|---|
| exact id | 97.83% |
| via station code (id changed, e.g. Montparnasse in April 2025) | 2.12% |
| unknown station (IDFM's marker) | 0.05% |
| unmatched | 0.001% |

**Surface** (`dim_line`): `id_groupoflines` matches the line reference;
`code_stif_ligne` matches nothing (0 of 726).

| Match | Share of validations |
|---|---|
| exact id | 88.2% |
| unknown line (IDFM's marker) | 9.3% |
| not in current reference | 2.5% |

## 4. Today's references name the past well enough

GTFS covers 2026-09-24 → 2026-10-26, after the validation data ends. It was
measured, year by year, how much of each year it can still name:

- **Rail:** ≥ 99.89% of validations named in every year, 2015–2025.
- **Surface:** lines missing from today's reference are 1.5–4.7% per year.

So current GTFS (stations) and the current line reference (lines) are used
for all years. Names shown are today's names; the name used at the time is
kept in `validation_name`. GTFS timetables are not used.

## 5. Known defects are data, not footnotes

`quality_flags.csv` lists every defect that changes what a number means,
each with its period, what it affects, and a SQL condition. The build
checks each flag against the data and stops if a flag matches nothing or
matches rows outside its stated period.

| Defect | Affects | Scale |
|---|---|---|
| Counts below 5 suppressed, 2015 → mid-2017 | totals | 1,070,068 rows of unknown value |
| Validations with no line, 2015 → mid-2017 | per line | 916M; **48% of 2015 surface** |
| Validations with no line, mid-2022 → 2025 | per line | 130M |
| Validations with no station | per station | 9.3M, under five different markers |
| Station with a placeholder id | per station | 15.3M, resolved via station code |
| Fare labels relabelled 2023-07-01 | per category | see decision 2 |

## 6. Hourly profiles are kept whole, and marked when inconsistent

The hourly tables give each hour's share of a typical day's taps. They have
no date: the half-year or quarter is only in the file name, so the period is
recovered from it.

A profile should add up to 100%. Measured:

| | Profiles | Add up to 99-101% | Over 101% | Under 99% |
|---|---|---|---|---|
| Rail | 94,772 | 99.8% | 149 | 84 |
| Bus and tram | 147,371 | 97.7% | 3,004 | 353 |

Over 101% almost always means several profiles stacked under one key, with
the same hour listed more than once (e.g. Porte de Clichy, up to 845%). Under
99% means hours are missing. Neither can be repaired, so every row is kept,
`profile_ok` marks the usable ones, and a warning covers the rest.

Splitting profiles by operator was tried and changed nothing (99.8% either
way), so that was not the cause.

Missing hours mean zero taps (stations closed at night), not unknown.

## 7. One database file for the AI

`data/model/ridership.duckdb` holds the four fact tables, the station, line
and ticket-type tables, and the warnings. Built by `model/build_*.py`.

- **Rows and taps are checked in = out** for every table; the build deletes
  the file if any differ.
- **Warnings refer to the final tables**, so the AI checks them against what
  it actually queries. `warnings_between(start, end)` returns the warnings
  for a period. The build rejects a warning whose stated period is more than
  a month wider than the rows it covers, so no warning is shown for years it
  does not affect.
- **Every table and column has a plain-English description** stored in the
  database (`descriptions.csv`). The build stops if any is missing.

Checked by asking real questions: metro and train taps per year match the
earlier results exactly, for all eleven years.
