# Committed fallback data

The pipeline uses these copies only if its live sources are unavailable, so that a clone can still run to completion. The run report records which source each run used.

| File | Source | Retrieved | SHA-256 |
|---|---|---|---|
| `wind_2020.xlsx` | [City of Cape Town open data portal, "Wind"](https://www.arcgis.com/sharing/rest/content/items/31ef242a23484e79bbb19d6b29203179/data) | 6 November 2026 | `21e7488066e8b50fd5ff4883117e359507d61ee6457c3ad091243b8800613873` |
| `suburb_saxonsea.geojson` | City of Cape Town "Official Planning Suburbs" feature service, filtered to Saxonsea | 6 November 2026 | `df716659f0a8132b3d5ee6f783cff61551b92f2be8d613c8bc9fc48e908d2832` |

Both are subject to the City's [open data terms of use](https://www.capetown.gov.za/General/Terms-of-use-open-data).

The challenge's wind link returned HTTP 404 throughout testing on 6 November 2026, so this workbook could not be compared with the file behind that link. It covers the same period and names the Atlantis site.
