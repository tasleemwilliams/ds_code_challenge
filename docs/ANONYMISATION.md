# Why the output is anonymised

The input is 9,903 requests near Saxonsea: 7,848 were published and 2,055 queued for manual review. South Africa's POPIA treats data as de-identified only if no reasonably foreseeable method can re-identify it, so each route back to a resident was considered. This is an engineering judgement, not legal advice; the City's information officer should confirm it before public release.

## What was removed or coarsened

| Route to a resident | Treatment |
|---|---|
| Notification and reference numbers could link to customer records | Removed |
| Coordinates can point to one property | Removed. The H3 level 8 hexagon remains, with corners 494–532 m from its centre |
| Exact times could match a site visit | Creation and completion times floored to 6-hour local blocks. The two blocks together give duration to about 12 hours, which describes the service, not the resident |
| Suburb can narrow a boundary-crossing hexagon to finer than 500 m | Removed |
| Branch and section can reveal debt or enforcement teams | Removed |
| Hourly wind, matched against the public file, reveals the exact hour | Replaced by a 6-hour mean, identical across the block |
| Row order could reflect notification order | Rows sorted by block, hexagon and code |

Columns are published from an allow-list, so a new source column cannot leak by accident. The pipeline re-checks the final file and stops if any guarantee fails.

## What went to manual review

- **Small groups (874):** fewer than five records share a hexagon, code and cause code. Rare requests in small areas most easily point to one household.
- **Financial status (661):** for example indigent applications, arrears and reconnections.
- **Tenancy and property (314):** requests revealing council housing tenure or property matters.
- **Alleged wrongdoing (206):** for example suspected tampering.

Group sizes are counted after sensitive records leave, so removing them cannot leave a small group behind. Patterns deliberately over-flag: "No Tampering Found" is queued because it shows an investigation took place, and a reviewer can release it.

The queue, `output/restricted/manual_review_queue.csv`, keeps the original fields plus empty decision and notes columns, and must be handled as personal information.

## Remaining risk

The data is not risk-free. 53.7% of published rows are unique on hexagon, 6-hour block and code. Requiring five matches at that level would remove about three-quarters of the subsample, so the group rule applies across the year instead.

Someone who saw a repair, or knows a neighbour reported one, might recognise that row, but would learn little beyond its completion block. Without that outside knowledge, a row cannot be tied to one household in a hexagon of about 0.7 km². The City still holds the source records, so the file is only anonymised for people without access to them.

No population figures were available, so a sparsely populated hexagon could carry more risk than its request count suggests; before release I would check hexagons against census data.
