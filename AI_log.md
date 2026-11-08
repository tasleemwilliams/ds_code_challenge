# AI log

This log records the work I asked AI assistance to undertake for this assessment, as the challenge README requires.

- **Tool:** Claude Code (desktop app)
- **Model:** Claude Opus 5.5 (`claude-opus-5-5`)
- **Token counts:** the figures below are the session's context-window size when the entry was written. The tool does not expose a cumulative input and output total, so these are not billed totals.

## Session 1: 6 November 2026, setup and source testing

Tokens at the end of this entry: about 154,000 in the context window.

| # | Prompt | What the AI did |
|---|--------|-----------------|
| 1 | `look at my email for Assessment: Head Data Engineer` | Found and summarised the assessment emails: deadline, scope and how to submit. |
| 2 | `Yes, read the repo` | Read the README and outlined sections 1, 2 and 5 and their rules. |
| 3 | `Yes, start` | Forked and cloned the repo, then tested each data source with throwaway scripts outside this repo. No solution code was written. |

### Findings from testing

- S3 Select works with the supplied credentials and refuses anonymous access. It returned all 3,832 level 8 hexagons in about 3 seconds, with geometry identical to the reference file.
- Computing H3 level 8 indices from latitude and longitude matched `sr_hex.csv.gz` for all 729,270 rows with coordinates. Three of those rows fall in hexagons outside the city's hexagon set.
- The README's wind URL returned HTTP 404 on every attempt from a default command-line client, and its document host did not resolve in DNS. The City's open data portal has a "Wind" workbook covering 2020 with Atlantis columns.
- Atlantis wind readings exist for only 2,324 of 8,784 hours in 2020.
- A name search for suburbs near Atlantis also matched Beacon Valley, which is in Mitchells Plain. Name matching alone is therefore not a safe way to choose a suburb.

### AI errors and corrections

- The AI warned that S3 Select might be unavailable because AWS had closed it to new customers. Testing showed it works with the supplied credentials. The AI found this itself, so it is not an example of me correcting it.
- One AI request set a browser user-agent to test the wind URL. The tool's permission check blocked it, it never ran, and the AI did not retry it that way.

## Session 1 continued: 6 November 2026, building the pipeline

Tokens at the end of this entry: about 296,000 in the context window.

| # | Prompt | What the AI did |
|---|--------|-----------------|
| 4 | `Yes, build it` | Wrote the pipeline, configuration, tests and documentation in this repository, following a design it had proposed and I had approved. It then ran the pipeline on the real data and from clean copies on Python 3.9 and 3.12. |

### Problems the AI found and fixed in its own work

These are not examples of me correcting the AI.

- Its first version took 24.6 seconds to assign hexagons, because `np.isin` sorts text values. A hash lookup cut this to about 0.5 seconds.
- Its first version peaked at about 1.6 GB of memory by holding both service request files at once. Loading them in turn cut this to about 600 MB.
- A test helper left the Atlantis hexagon out of its generated data, which failed four integration tests.
- Its first anonymisation note exceeded the 500-word limit.
- It wrote a Beacon Valley distance of 54 km without calculating it. A later calculation gave about 56 km.
- Its first wording of the POPIA test was looser than the "reasonably foreseeable method" wording it then found in secondary commentary. The statute itself was not checked.

## Corrections I made to the AI's work

<!-- Add real cases here as they happen. The README gives credit for at least one. Remove this comment before submitting. -->
