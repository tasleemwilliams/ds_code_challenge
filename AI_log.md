# AI log

This log records the work I asked AI assistance to undertake for this assessment, as the challenge README requires: the tools and models, the prompts, token usage where the tool exposes it, and the cases where I corrected or rejected the AI's work.

## How I used AI, and why

I applied for an AI-native Head of Data Engineering role, so I treated the assessment as a test of how I run agents, not only of what I can code. The approach is one I use to understand a problem space before I go deep into it: run several models through the same problem, review what each finds, and build on the stable ground between them.

**Friday 6 November, understand the shape.** I read the brief on GitHub, then let Claude Opus 5.5 in Claude Code read it, test every data source, propose a design and run one iteration to completion. That build is this repository. Inspecting it gave me the shape of the problem: where the judgement calls were (the arc-minute reading, the dead wind endpoint, what "anonymised" has to mean under POPIA), and what a few hours of supervised agent time produces against this brief.

**Friday evening to Saturday morning, test the other harnesses.** I then deliberately put the same brief through two other models on a different harness, Hermes Agent via OpenRouter: Qwen3.8 Flash (23.6 million tokens, USD 0.53), which also produced code, and DeepSeek v4.1 Flash (`deepseek/deepseek-v4.1-flash`). The DeepSeek run produced a full solution to all six sections for USD 0.81 and 98.6 million tokens, over about three hours on the morning of 7 November. That made three builds from three models. The point was not to get three answers; it was to see what each model found, where they agreed, and how each behaved unsupervised on an assessment of this kind.

**Saturday and Sunday, review and choose.** With three models' views of one problem, I used a fourth, Claude Fable 5.1 in Claude Code, to review the two most complete builds independently of the sessions that produced them: full source read, fresh clone and run in an empty directory, browser check of generated outputs, recomputation of reported figures. The review is where the corrections below come from. Where the builds agreed, I treated the answer as stable. Where they disagreed, the review and my own reading decided. This repository was chosen on that evidence as the baseline, and the deep work since then has gone into it.

The build itself took a few hours of my time, as the brief anticipated. The rest of the weekend went on the comparison and the checking, because for this role that is the work. The principle is not to over-specify at the start, but to see what a model does with the problem unaided, and then to become demanding about the answer: the spec first, then verification.

On Friday I gave the agent very little direction, on purpose: the first iteration was for me to see how the model would read the problem, not to steer it. The thresholds, the choice of suburb, the quasi-identifier set and the 500-word justification were all proposed by the model. They are in the submission because they held up on Saturday and Sunday when I put them against the other two builds and the independent review, not because I approved them on the day.

**What the model comparison showed.** The two flash-tier builds got roughly seventy percent of the work right, and it was the same seventy percent: S3 SELECT ran, the H3 join matched the City's column, the dead wind link was found and a mirror located, the auto-closure pattern in the data was noticed, fallbacks and tests existed. What failed was one kind of thing, reasoning about consequences a step or two out: coarsening time and then joining hourly wind that undoes it, computing a "centroid" from the very points being hidden, writing a result narrative before the result, declaring success without a clean run. Opus 5.5 made that class of error less often, though not never; the duration gap below is the same kind of miss. Two cautions: this is one run per model on one problem, not a benchmark, and the harness differed along with the model, so part of the gap may be tooling. The useful conclusion is narrower than "bigger model better": the cheap passes are not there to compete with the main build but to produce independent answers fast enough that disagreement becomes a signal. Where all three agreed I had a stable floor; where they disagreed I had the list of places a person needed to think, for USD 1.34, against about USD 57 for the build they were compared with.

**What the first pass bought, and what it did not.** The unsupervised build reached failure modes that a few hours of human work would not: it joins S3 SELECT records split across stream events and rejects a stream with no End event, it treats an HTTP 200 error page as a failed download, it finds the wind station's columns by header rather than position, and it guards the suburb centroid against a same-named suburb 56 km away. Each of those is a bug a team usually meets in production; here they were handled on day one. It also gave me the shape of the problem within an afternoon: which rows lack coordinates, that the wind link is dead, that the station was off for half the year, where the judgement calls sit. And it made three independent first passes affordable, one of them for USD 0.81, so the choices that survived comparison are the ones I trust. The limits are as important. The first pass was wrong in ways only a person caught: the duration gap in this build, and in the other build an hourly-wind column that identifies the hour, a "centroid" that was the mean of real addresses, and report prose that contradicted its own numbers. It also cannot tell you it is finished; the other build reported success and fails a fresh clone. And it over-delivers structure, so someone has to decide what to keep. The agent is the fastest way to find out what the problem is. The engineer's job moves to deciding, verifying and cutting.

Cost: the Qwen run cost USD 0.53 for 23.6 million tokens and the DeepSeek build USD 0.81 for 98.6 million (OpenRouter usage; the panel reports totals, not the prompt/completion split). The Opus 5.5 build that is this submission would have cost about USD 57 at API list price, and the independent review about USD 50. So the two cheap passes together cost under USD 1.50, the build about seventy times the cheap build that failed a clean clone, and the review about as much as the build. On this task, verification cost as much as construction. The Claude sessions ran on a subscription and the tool reports context-window size rather than billed tokens; those figures are given per session below.

## Tooling

| Tool | Model | Used for |
|---|---|---|
| Claude Code (desktop app) | Claude Opus 5.5 (`claude-opus-5-5`) | Source testing, pipeline, tests, documentation (this repository) |
| Hermes Agent via OpenRouter | DeepSeek v4.1 Flash (`deepseek/deepseek-v4.1-flash`) | Independent full-challenge build, used for comparison only, not submitted |
| Claude Code (desktop app) | Claude Fable 5.1 (`claude-fable-5-1`) | A five-minute side check on 6 November; the independent review of both builds on 7 and 8 November; commit sequence; this log |
| Hermes Agent via OpenRouter | Qwen3.8 Flash | Comprehension comparison on 6 November under the same harness as the DeepSeek run; 23.6 million tokens, USD 0.53 |

**Token counts.** Claude Code writes per-message usage to its session transcripts, so the Claude figures below are summed from those files: output tokens, cached input read, and cached input written. The Claude sessions ran on a subscription; the dollar figures are what the same tokens would cost at API list price (Opus 5.5: USD 4 / 20 per million input / output, 0.20 per million cache read, 5 per million cache write; Fable 5.1: 10 / 50 / 0.25 / 12.50), not a bill. OpenRouter reports billed totals per model and hour.

## Session 1: 6 November 2026, setup and source testing (Claude Opus 5.5)

Session usage (both parts of Session 1 and Session 2 are one transcript): 244 model turns, 07:55 to 14:59 SAST; 1.77 million output tokens, 42.1 million cached input tokens read, 2.63 million written; about USD 57 at list price. A separate five-minute Claude Fable 5.1 session the same morning (22 turns, 21,000 output tokens, about USD 7 at list price) was used for a side check and produced no code.

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

### AI errors it found itself

- The AI warned that S3 Select might be unavailable because AWS had closed it to new customers. Testing showed it works with the supplied credentials.
- One AI request set a browser user-agent to test the wind URL. The tool's permission check blocked it, it never ran, and the AI did not retry it that way.

## Session 2: 6 November 2026, building the pipeline (Claude Opus 5.5)

Usage is included in the Session 1 figure above.

| # | Prompt | What the AI did |
|---|--------|-----------------|
| 4 | `Yes, build it` | Wrote the pipeline, configuration, tests and documentation in this repository, following a design it had proposed and I had approved. It then ran the pipeline on the real data and from clean copies on Python 3.9 and 3.12. |

### Problems the AI found and fixed in its own work

- Its first version took 24.6 seconds to assign hexagons, because `np.isin` sorts text values. A hash lookup cut this to about 0.5 seconds.
- Its first version peaked at about 1.6 GB of memory by holding both service request files at once. Loading them in turn cut this to about 600 MB.
- A test helper left the Atlantis hexagon out of its generated data, which failed four integration tests.
- Its first anonymisation note exceeded the 500-word limit.
- It wrote a Beacon Valley distance of 54 km without calculating it. A later calculation gave about 56 km.
- Its first wording of the POPIA test was looser than the "reasonably foreseeable method" wording it then found in secondary commentary. The statute itself was not checked.

## Session 3: 7 November 2026, independent DeepSeek build (not submitted)

A full solution to all six challenge sections, built by a DeepSeek agent in about three hours for USD 0.81 and 98.6 million tokens. It is not part of this submission. It exists in this log because reviewing it is where most of the corrections below come from. Its own AI log claimed nine self-corrections.

## Session 4: 7 and 8 November 2026, independent review (Claude Fable 5.1)

Session usage to 8 November: about 205 model turns, 0.32 million output tokens, 55.9 million cached input tokens read, 1.65 million written; about USD 50 at list price, roughly the same as the build it reviewed.

| # | Prompt | What the AI did |
|---|--------|-----------------|
| 5 | `review this assessment independently - <challenge URL>` | Cloned the upstream brief, read every file of the DeepSeek build, ran it from a fresh clone, opened its dashboard in a browser, recomputed report figures from its processed data. Wrote a findings document. |
| 6 | `What are the Data Engineering tasks` / `walk me through the tasks` | Explained sections 1, 2 and 5 and the judgement each one hides. |
| 7 | `how would you go about doing this if 1) you use ai and 2) you don't` | Two 48-hour plans; the AI plan allocates most hours to verification. |
| 8 | `Ai native Head Data Engineer` | Reviewed this repository the same way: clean-copy run (65 s, exit 0), 112 offline tests, ruff, design docs against code and run report. Wrote a side-by-side comparison and recommended this repository. |
| 9 | `Yes, start` | Built the seven component commits in this repository and drafted this log. |

## Corrections: AI work I checked and rejected or changed

The brief asks for at least one case where the AI did work that I then corrected or improved on. I did not correct the Opus 5.5 build while it ran; that was deliberate, as described above. The corrections came afterwards, from comparing three builds and reviewing two of them in depth. These are the cases, with what was measured.

1. **The DeepSeek anonymisation leaked the exact hour it claimed to have removed.** Its published file kept hourly wind direction and speed joined from the public wind workbook. Any pair of readings identifies the creation hour, which undoes the 6-hour coarsening. Its own AI log recorded a "fix" to the wind join logging and never noticed the leak. This repository replaces hourly wind with a block mean (`anonymise.aggregate_wind_to_blocks`), identical for every record in the block, and the independent review confirmed that is what the code does.
2. **Its "500 m location" was the mean of real addresses.** It computed `hex_lat`/`hex_lon` as the mean coordinate of the requests in each hexagon. With a minimum group of three, that is the mean of three households. This repository publishes only the H3 index; the cell centre is derivable by anyone and carries no request-level information.
3. **Its k-anonymity design withheld 87% of the data.** Grouping on hexagon and 6-hour bucket over a one-year, one-suburb sample makes almost every record unique, so 967 of 1,117 rows went to manual review and 150 were published. That meets the letter of the brief and defeats its purpose. This repository groups on hexagon, code and cause code with a minimum of five, routes sensitive request types separately, and publishes 7,848 of 9,903 with 2,055 queued. The residual risk is stated in `docs/ANONYMISATION.md`.
4. **Its suburb centroid was not a suburb centroid.** It averaged the coordinates of requests labelled "Atlantis Industrial" rather than computing the centroid of an official boundary. This repository queries the City's Official Planning Suburbs service, computes the polygon centroid, guards it with an Atlantis bounding box, and commits a fallback copy with its checksum.
5. **It did not clone and run.** A fresh clone of the DeepSeek build exits 1 at section 5 because `openpyxl` was used but never declared; it was present in the agent's environment. The brief says reviewers will not fix this. This repository declares both `openpyxl` and `odfpy`, and the independent review ran it from a clean copy with no caches: 65 seconds, exit 0, numbers identical to the README.
6. **Its reports contained claims its own numbers contradicted.** A significance claim where one p-value was 0.17, a "highly concentrated" 14%, a lockdown narrative whose anomaly table was a late-January event in a different department, a model description for a network that had been replaced. None of these are in this repository, but they set the standard for the final check I applied here: every figure in the README, `DESIGN.md` and `ANONYMISATION.md` was read against `output/run_report.json` before submission.
7. **This repository's own log had an empty corrections section.** The Opus 5.5 session left a TODO comment where this section should be. The section above is the result of actually doing the comparison the TODO deferred.
8. **A gap the review found in this repository, now stated in the note.** The anonymised file publishes both the creation and completion 6-hour blocks, so a request's duration is recoverable to within about 12 hours. The Opus 5.5 build did not raise this and `ANONYMISATION.md` did not mention it. The design stays, because duration describes the service rather than the resident and the group-size rule still applies, but the note now says so explicitly, with two sentences trimmed elsewhere to stay under the 500-word limit.
9. **A geometry figure the model asserted and the final check disproved.** `ANONYMISATION.md` said a level 8 hexagon's corners lie 520–531 m from its centre. Recomputed with the H3 library over all 3,832 city cells on 8 November, the range is 494–532 m; the model's figure matched no cell. Corrected, along with two smaller points found in the same pass: the Atlantis Industrial subsample came back as 444 rows against the 445 recorded on 6 November, so the design note now says "about 445", and the wind gap list omitted December, which also has no readings.

## Note on commit history

The code was written in one supervised session on 6 November and committed as seven component commits on 8 November, after the review above. The commit sequence reflects the structure of the work, not the minute-by-minute order in which it was produced. I would rather say that plainly than present a reconstructed history as if it were live.
