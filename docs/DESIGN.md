# Design notes

These notes record the choices behind the pipeline, and the evidence for each. Figures come from a run on 6 November 2026 unless stated otherwise.

## Section 1: extraction and schema conformance

**S3 Select.** The query filters on `resolution = 8` and projects only the fields needed. It scans the whole 108 MB file, because S3 Select cannot skip ahead in a JSON document, but returns only 2 MB. It took about 3 seconds. S3 Select refuses anonymous callers, so the challenge's public dummy credentials are fetched at run time, held in memory and never logged or saved.

**Incomplete results.** One record can span two stream events, so payloads are joined before parsing. A stream with no `End` event is rejected, because a dropped connection would otherwise look like a smaller, valid result.

**Reference check.** All 3,832 hexagons match `city-hex-polygons-8.geojson`. Coordinates are compared with a tolerance of 1e-9 degrees, under a millimetre on the ground, so harmless float formatting cannot fail a run.

**Conformance score.** The schema is in [`config/hex_schema.yaml`](../config/hex_schema.yaml). The score is the weighted share of rule checks passing across all features.

| Score | Status | Effect |
|---|---|---|
| 0.99 or more | PASS | Continue |
| 0.95 to below 0.99 | WARN | Continue and log failing rules |
| Below 0.95 | FAIL | Stop |

The thresholds are based on what each score means with these weights:

- A wholly malformed hexagon costs about 0.03% of the score, so isolated bad records warn at worst rather than stopping a run.
- About 1% of hexagons must be wholly malformed to leave the PASS band.
- The lowest-weighted rule carries 1/19 of the weight. If any single rule fails on every feature, the score falls to 0.947 at best and the run stops. A systematic fault can never pass.

## Section 2: joining requests to hexagons

**Method.** The H3 index is computed from each request's coordinates and checked against the extracted hexagons. This equals a point-in-polygon join for H3 cells, but a point can never land in two hexagons or between them, and it takes about half a second for 941,634 rows.

**Result.** All 941,634 requests match `sr_hex.csv.gz`: 729,267 joined, 212,364 had no coordinates and got index `0`, and 3 fell outside the city's hexagons.

**Those 3 requests.** They keep their computed index, because that is what `sr_hex.csv.gz` records and it is still their true location, but they count as join failures. Two of them share the same coordinates.

**Failure threshold: 0.1% of requests with coordinates.** A failure means a request has coordinates but no city hexagon.

- The observed rate is 0.0004%, so normal noise sits about 240 times below the threshold.
- 0.1% is about 729 requests. The median hexagon with requests holds 160, the 90th percentile 986 and the busiest 3,815.
- So losing one busy hexagon, or about five typical ones, stops the run. The likely systematic faults trip it, such as a truncated hexagon file or swapped coordinates, while a few bad records do not.

**Missing coordinates: 35% of all requests.** These are not join failures, so they have their own check. The observed rate is 22.6%, and it moves only between 19.7% and 24.8% from month to month, so 35% sits well above anything seen in 2020. The rate is a property of how residents report rather than of data quality (Urban Mobility is 65% missing, Water and Sanitation 10%), so it cannot be derived the way the join threshold is; the ceiling is empirical. A rise past it would suggest a broken source file, which the failure rate alone would miss because its denominator would shrink.

## Section 5.1: subsample

**Suburb: Saxonsea.** No official suburb is simply called Atlantis. Saxonsea has the most requests of the Atlantis-area suburbs checked (5,116), and 45.4% of the 9,903 requests within its radius have wind data. Atlantis Industrial is the only one named after the town, but yields only about 445 requests, 28.8% with wind. The suburb is a config setting.

**Centroid.** It is computed from the polygon in the City's "Official Planning Suburbs" service. Treating longitude and latitude as planar is sound here: over a few kilometres, ground distance is almost exactly a linear rescaling of those coordinates, and a centroid is unchanged by linear rescaling.

**"Within 1 minute".** I read this as 1 arc-minute of great-circle distance, about 1.85 km. A box of ±1 minute in each axis would be narrower east to west, since a minute of longitude is about 1.54 km here, and would select a different area.

**Location guard.** The centroid must fall inside a configured Atlantis area. Beacon Valley shows why: its name resembles Beacon Hill in Atlantis, but its centroid is about 56 km to the south-east, in the Mitchells Plain area.

## Section 5.2: wind

**The unreliable endpoint.** The reasoning is in the docstring of [`cct_pipeline/wind.py`](../cct_pipeline/wind.py). The order is cache, then each live source with bounded retries, then a committed copy, and every candidate must pass the same content validation.

**The README's link was down.** On 6 November 2026 it returned HTTP 404 from both a command-line client and a web browser, and its document host did not resolve. The run therefore uses the 2020 wind workbook from the City's open data portal. With the original file unavailable, I could not confirm the two files are identical. The pipeline still tries the README's link first, in case it returns.

**The failure is not specific to the wind file.** The Internet Archive's Wayback Machine holds no capture of the wind file, so I cannot show that this link ever worked. It does show the same download handler serving other datasets in May 2022, November 2022 and May 2025. On 6 November 2026, the two of those files I retested (from the November 2022 and May 2025 captures) and the handler with no file specified all returned 404. This suggests the download service itself is no longer available, so a fallback that only retries this link would not be enough.

**Data gaps.** Atlantis has readings for only 2,324 of 8,784 hours (26.5%), and none from April to September or in December. Of the 9,903 subsample requests, 4,499 (45.4%) have a reading. Gaps stay empty with the source's reason (`NoData`, `Down` or `<Samp`), because estimating across months would invent data.

**Coverage check: 25%.** A run stops below this, as a sign the join or time alignment has broken. It sits below the 28.8% seen for the least-covered suburb tested, so suburb choice alone should not trip it.

**Time alignment.** Requests are converted to South African time and floored to the hour. The hour labels mark the start of each averaging period, because the file's stated range runs to 23:59 on 31 December while its last label is 23:00. The file does not state a time zone, so local time is an assumption.

## Section 5.3: anonymisation

See [ANONYMISATION.md](ANONYMISATION.md).

## Performance

Measured from a clean copy with an empty cache on Python 3.9:

| Stage | Seconds |
|---|---|
| Section 1 (S3 Select, schema and reference checks) | 4.2 |
| Downloading the service request files | 48.7 |
| Section 2 without downloading (load, join, write, validate) | 7.9 |
| Section 5 (suburb, wind, anonymisation) | 8.0 |
| **Total** | **68.8** |

Peak memory was about 650 MB. Later runs reuse cached downloads and take about 11 seconds.

Two changes made the biggest difference, measured against the first working version:

- Replacing `np.isin` with a hash lookup cut hexagon assignment from 24.6 to 0.5 seconds.
- Loading one service request file at a time, keeping only the identifiers from the first, and letting pandas read in chunks cut peak memory from about 1.6 GB to about 600 MB.

Repeated text columns are also stored as categories, to reduce memory further. I did not measure that change on its own.

## What I would do next

- Replace S3 Select in any long-lived system. AWS has closed it to new customers, so it is a poor long-term dependency, even though it works for this challenge.
- Partition the source by resolution, so that a query need not scan the whole object.
- Check hexagons against population data as part of anonymisation.
