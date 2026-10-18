# Drawing2CAD

[![License: AGPL-3.0-or-later](https://img.shields.io/badge/License-AGPL--3.0--or--later-blue.svg)](LICENSE)

Drawing2CAD turns a 2D technical drawing into a 3D solid model in STEP format (AP242). A language model reads the drawing and builds the part exclusively through the tools of a local MCP server; the server has no network access. After every build step the server checks that the solid is valid and matches the captured specification (overall size, holes, STEP re-import), and lists printed dimensions that do not occur in the solid. Every assumption that is not stated in the drawing is marked orange in the model and listed with its reason in a review list.

Contents: [Requirements](#requirements) · [Quick start](#quick-start) · [Local models](#local-models) · [Processing a drawing](#processing-a-drawing) · [Requirements matrix](#requirements-matrix) · [Results](#results) · [Limitations](#limitations) · [Data flow and privacy](#data-flow-and-privacy) · [Configuration](#configuration) · [Project layout](#project-layout) · [Licenses](#licenses)

## Requirements

| Requirement | Purpose |
| --- | --- |
| [Docker](https://docs.docker.com/get-docker/) (Docker Desktop on Windows and macOS) | build the image, tests, demo, CAD server |
| about 8 GB RAM, 4 GB free disk space | the image is about 3 GB |
| [uv](https://docs.astral.sh/uv/) | only for `./start.sh` and the scripts on the host; installs Python 3.12 itself |
| poppler-utils | not required; drawings are rendered with PyMuPDF, poppler is only a fallback. The image contains it for the text layer |
| Access to a language model | only for runs on a drawing: Claude (login or API key), Ollama or an Anthropic-compatible endpoint; tests and demo need none |

A quick check without the full suite: `docker run --rm d2c pytest -q tests/test_demo.py`.

## Quick start

Build, test, demo (no language model, no network, no sample data):

```
docker build -t d2c .
docker run --rm --network none d2c pytest -q
docker run --rm --network none d2c python -m d2c.demo
```

Duration on a current laptop: about 3 to 5 minutes for the first image build, 15 to 20 minutes for the tests depending on the machine, under a minute for the demo; about 30 minutes under emulated amd64 (for example on Apple Silicon with `--platform linux/amd64`).

The demo builds three sample parts from `d2c/demo` (bracket, shaft with chamfer, flange with H7 fit), prints one `PASSED` or `FAILED` line per check and ends with `3 of 3 parts exported, all checks passed`. To keep the files: `mkdir -p out && docker run --rm --network none --user "$(id -u):$(id -g)" -v "$PWD/out":/out d2c python -m d2c.demo --out /out` (`--user` makes the files yours and avoids permission errors on Linux).

Excerpt from the review list `review.md`, section "Assumptions and open items", with the single assumption of the demo (the plate thickness is not dimensioned in the drawing and was measured):

```
## Assumptions and open items
| ID | Dimension / feature | Issue | Assumption | Reason | Source | Marked faces in STEP |
|---|---|---|---|---|---|---|
| rv1 | t10 | missing | Plate thickness 10 mm | The plate thickness is not dimensioned; measured in the front view at scale 1:1 | - | Faces: 2 |
```

### View the sample result

`examples/demo/` holds the results of all three demo parts: `demo_bracket`, `demo_shaft` and `demo_flange`. Each folder contains STEP (AP242 with PMI), GLB for the 3D view, `review.md` (review list), `report.json`, `spec.json`, `ops.json`, `marks.json` and `pmi.json`. The web UI shows them without a run of your own:

```
D2C_RESULTS=examples/demo D2C_DATA=d2c/demo uv run python -m d2c.web
```

Then open `http://localhost:8080` (the sample drawings are in `d2c/demo`). CI and tests rebuild the samples and compare volume and bounding box with the checked-in `report.json` files (`python scripts/check_example.py`; `--update` rewrites it).

### Web UI with your own drawing

On your own machine (Docker and uv required; macOS and Linux):

```
./start.sh
```

`start.sh` builds the image, sets up the Python environment and starts the UI on `http://localhost:8080`. The CAD tool runs in a Docker container without network for each run. Drop a drawing (PDF, PNG or JPG), change the part name and model if needed: the run starts automatically on drop (the option "Upload only" suppresses this; then click "Create STEP"). The option "Update existing model (new revision)" is only available for an existing part name: it changes the model to match the new drawing instead of rebuilding it, and the review list shows the changes. The run is shown live and reattaches after a page reload; afterwards the STEP file, 3D view and review list are available. Results are stored under `results/<part>/`; a new run moves the previous state to `results/<part>/versions/<timestamp>/`, and a failed export leaves the last good STEP and GLB untouched.

Everything in containers (Claude then needs `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN` in `.env`, see `.env.example`):

```
mkdir -p drawings results tables
docker compose up --build
```

**Access and protection:** the UI has no login. `./start.sh` binds it to `127.0.0.1` (default of `--host`), Compose publishes `127.0.0.1:8080:8080`. For access over a network, put a reverse proxy with authentication in front of it. Built-in limits: Host header check (`D2C_ALLOWED_HOSTS`, 400 for other hosts), `Origin` check on `POST /api/runs` (403 for a foreign origin), at most `D2C_MAX_RUNS` concurrent runs (default 2, then 429), 409 for an active run on the same part, model check against `/api/models` (400), 50 megapixel limit for images (413), 400 for damaged or password-protected PDFs, part names up to 64 characters. The container runs as user `app` (uid 1000) with a read-only file system and `tmpfs` for `/tmp`; `start.sh` and Compose start it that way.

**Windows:** `start.sh` is a Bash script. On Windows, install Docker Desktop (WSL2 backend) and run `docker compose up --build` in PowerShell, with `ANTHROPIC_API_KEY` in a `.env` next to `docker-compose.yml`. Alternatively run `./start.sh` in WSL. The Python parts outside the container are not designed for Windows (the sandbox uses Linux and macOS features); everything else runs in the container. Commands with folders read `docker run --rm -v "${PWD}/results:/app/results" d2c ...` in PowerShell.

Models in the UI:

- **Claude** (Sonnet, Opus, Haiku): with `./start.sh` through the Claude Code login, in the container with a token or API key. If the UI finds no login, Claude is disabled in the selection.
- **Ollama:** all locally installed models. The server needs a large context: `OLLAMA_CONTEXT_LENGTH=64000 ollama serve`.
- **OpenRouter or another Anthropic-compatible endpoint:** `D2C_LLM_BASE_URL`, `D2C_LLM_API_KEY` and `D2C_LLM_MODEL` in `.env`.

### Agent without UI and batch run

```
D2C_DATA=drawings uv run python -m d2c.agent "Part/Drawing.pdf" --part partname --model sonnet
D2C_DATA=drawings uv run python -m d2c.agent.batch drawings --jobs 3
```

The first run after installation may take a few minutes to start while the CAD libraries load.

`d2c.agent.batch` processes every PDF below the given folder as a part of its own: a subfolder with exactly one PDF is named after the folder, with several PDFs `<folder>_<file>`, a PDF in the root folder is named after the file. It writes `<out>/<part>/runs/*.jsonl`, plus `<out>/batch.csv` and `batch.json` with the columns `part`, `status`, `duration_s`, `cost_usd`, `turns`, `export_ok`, `review_items` and `reason`; `cost_usd` is empty if the model reported no cost. Resuming skips parts with a valid `report.json`; `--force` forces a rebuild. The exit code is 0 only if every part is finished.

| Option | Effect |
| --- | --- |
| `--jobs N` | parallel runs |
| `--model`, `--build-model`, `--base-url`, `--server` | model for reading, model for building, endpoint, MCP server (defaults from `D2C_LLM_*` and `D2C_MCP_URL`) |
| `--out DIR` | results folder (default `results`) |
| `--force`, `--no-script`, `--max-turns N` | force rebuild, disable `run_script`, limit the number of turns |
| `--max-usd X`, `--max-minutes Y` | cost and time limit per run; override `D2C_MAX_USD` and `D2C_MAX_MINUTES` |

### MCP server for your own client

The server speaks MCP over stdio and runs without network:

```
docker run -i --rm --network none --read-only --tmpfs /tmp --user "$(id -u):$(id -g)" \
  -v "$PWD/drawings":/data:ro \
  -v "$PWD/results":/app/results \
  d2c
```

`drawings/` contains the PDF drawings, `results/` receives the results. Over HTTP: `python -m d2c.server --http 127.0.0.1:8765`, endpoint `/mcp`. The HTTP endpoint has no authentication; bind it to `0.0.0.0` only inside an isolated network such as the internal Compose network. The server instructions `d2c/server/instructions.md` are longer than 4096 characters; the agent sets `CLAUDE_CODE_MAX_MCP_DESCRIPTION_LENGTH=16000` itself. If you use `claude -p` directly with the server, set this variable as well. In the agent the tools are named `mcp__d2c__<name>`. The build tools reply with a compact report (`ok`, `failed`, `next`, `notes`, `volume`, `bbox`); if the server reinterprets an argument, every reinterpretation is listed under `coerced`. `D2C_REPORT=full` returns the full report.

## Local models

Ollama needs a large context:

```
OLLAMA_CONTEXT_LENGTH=64000 ollama serve
```

The model must support tool calls. For a model without a Claude name on a custom endpoint, the agent switches to lite mode automatically: small tool sets per phase, full tool names and a short procedure in the prompt, two sessions (read, then build). `--lite` and `--no-lite` override this. `D2C_LLM_CONTEXT` sets the context window (default 64000), `D2C_LLM_THINKING` limits the model's thinking tokens.

On the command line:

```
D2C_LLM_BASE_URL=http://localhost:11434 D2C_LLM_MODEL=<model from ollama list> \
  python -m d2c.agent --part <part> <folder>/<drawing>.pdf
```

"Cloud reads, local builds": `--model sonnet` reads the drawing, `--build-model <model>` with `--build-base-url http://localhost:11434` builds the part from the specification. `--spec <file>.json` skips reading and builds from a finished specification. `--revise` updates an existing part (`results/<part>/spec.json` and `ops.json`) to a new drawing revision without rebuilding it: one session reads the new drawing, submits the specification with `keep_ops=true`, changes only the differences and exports; the `done` result contains `changes`.

```
uv run python -m d2c.agent "Part/Drawing_B.pdf" --part partname --revise
```

## Processing a drawing

1. **Upload:** PDF, PNG or JPG; images are converted to a single-page PDF.
2. **Read:** the model renders the pages (`render_drawing`), reads the text layer (`read_drawing_text`) and measures missing dimensions and the scale from the vector geometry (`measure_drawing`). It submits the result as a `PartSpec` (`submit_spec`): views, dimensions with printed text and grid zone, design features, notes, expected size and holes, review items. The server rejects a specification in which an assumed or uncertain dimension has no review item, an assumed dimension is unmeasured, or the title block scale clearly contradicts the geometry without a review item naming it. Reading gates refuse a first submission once if the model has not looked at every page, rendered a detail crop at 200 dpi or more, or captured the printed dimensions it measured; when an existing model is updated, they also refuse once if `expected` (bounding box or holes) moves to the values of the built model without any dimension changing.
3. **Build:** `extrude`, `revolve`, `add_holes`, `edge_finish` and `run_script` as a fallback. Every change rebuilds all operations in a fresh process and returns a machine-readable report. The model corrects with `edit_op`, `delete_op` and `move_op`.
4. **Check:** valid solid, overall size and holes against the captured specification (including whether a hole goes through), search for the printed dimensions in the solid, STEP re-import, PMI read-back. The seal "All checks passed" means consistency with the captured specification, not agreement with the drawing.
5. **Export:** if the checks pass, `export_step` writes STEP AP242 with semantic PMI, GLB, the check report and the review list `review.md`. With `variant="mid"` it additionally builds a model at the tolerance midpoints. Export gates refuse the first export once if a review item or note admits an omitted feature, a noted chamfer or draft has no operation, a measured circle has no counterpart in the solid, or a printed dimension is missing without a review item; the same state then passes with a warning in `review.md` (rules: specification, section 6.6).

Details, data model and interfaces: [system specification](docs/spec.md) (German).

## Requirements matrix

The IDs correspond to `docs/spec.md`, section 3. Status: **met**, **partial** (stating what is missing) or **limit** (deliberately not covered). The tests are in `tests/`.

| ID | Requirement | Implementation | Test | Status | Note |
| --- | --- | --- | --- | --- | --- |
| A1 | Recognise contours, dimensions, holes, basic features and notes from PDF, scan or image | `server/tools.py` (`render_drawing`, `read_drawing_text`, `measure_drawing`), `drawing/pages.py`, `drawing/text.py`, `drawing/vector/`, `spec.py` | `test_drawing`, `test_text`, `test_vector`, `test_server` | partial | Vector PDF complete. Scans and images: the model reads them visually; vector measurement is not available and not benchmarked (limit). Drawing notes are not captured in every part. |
| A2 | Flag ambiguity instead of interpreting it unchecked | `spec.py` (mandatory review items), `server/tools.py` (scale check, mandatory measurement), `quality/review.py` | `test_spec`, `test_server`, `test_session` | met | Every assumed or uncertain dimension needs a review item with a reason, otherwise `submit_spec` rejects it. |
| A3 | The 3D model is created exclusively through the MCP server, exported as STEP | `server/tools.py` (`export_step`), `agent/runner.py` | `test_server`, `test_agent`, `test_demo` | met | The agent has no tools other than the MCP tools. |
| A4 | The MCP server runs fully locally, without cloud dependency | `Dockerfile`, `docker-compose.yml`, `start.sh` | CI: tests and demo with `--network none`; `test_server` (HTTP) | met | Applies to the CAD server. The language model is separate and may run in the cloud (see data flow). |
| A5 | Scripts run isolated: no network, time limit | `cad/sandbox.py`, `cad/worker.py`, `cad/ops.py` | `test_sandbox`, `test_ops` | met | Separate process with time and resource limits; `run_script` only sees an allow list (`cq.<class>`, `math`, simple builtins, no imports or file access). Scripts cannot name file paths: `fontPath`, `fileName`/`filename` and similar arguments are rejected before the worker starts. The network block is the container, which runs as user `app` with a read-only file system. |
| A6 | Scriptable open-source CAD kernel based on OpenCascade | `cad/ops.py`, `cad/checks.py` | `test_ops`, `test_checks` | met | CadQuery 2.8.0 with OCP 7.9.3. |
| A7 | Few structured tools, free code only as a fallback | `server/tools.py` | `test_server` | met | 15 tools; `run_script` is the fallback and can be disabled in batch runs. |
| A8 | Check after every build: solid, dimensions, STEP re-import | `cad/checks.py`, `quality/review.py`, `session.py` | `test_checks`, `test_session` | partial | Solid and re-import are verified. The dimension check compares the model with the **captured specification**, not with the drawing: the seal "All checks passed" proves consistency, not that the drawing was read correctly. In addition, `report["dims_found"]` and the section "Dimensions without model reference" in `review.md` name the printed dimensions (tolerance 0.05 mm) that do not occur in the solid; the search also covers location dimensions of hole axes (axis to edge or plane, axis to axis with components in X, Y and Z). Holes carry `through` (through or not); a blind hole is allowed, a closed cavity without access fails the check. |
| A9 | Independent of the language model used | MCP standard, `server/instructions.md`, `server/coerce.py`, `server/guide.py`, `agent/` | `test_agent`, `test_guide`, `test_coerce` | partial | Claude, Ollama and Anthropic-compatible endpoints are connected; small local models get lite mode. Claude Sonnet is benchmarked; for two local models there is only a small sample (see results). |
| A10 | Mark unclear items in the model, with PMI or colours | `cad/export.py`, `cad/checks.py` | `test_export`, `test_checks` | met | Orange faces, named after the review item, in STEP and GLB. Assumed lengths, radii and diameters are also written as PMI with a note. If no PMI is created for an assumed dimension, `review.md` shows a hint ("Export notes"); orange faces and review item remain. |
| A11 | Assume missing dimensions from the geometry and justify them in the review list | `server/tools.py` (mandatory measurement), `spec.py`, `quality/review.py` | `test_server`, `test_spec` | met | An assumed dimension requires a measurement on the same drawing sheet and a reason; it only changes to "printed" after a new measurement. |
| A12 | Read tolerances, move to tolerance midpoint, check compliance | `quality/tolerances.py`, `quality/iso_tables.py`, `drawing/text.py`, `session.py` | `test_tolerances`, `test_session`, `scripts/freecad_check.py --mid` | partial | Tolerances and fits are read with built-in ISO 2768-1 and ISO 286-1 tables; `variant="mid"` builds at the midpoints and the report states compliance. Only dimensions bound through `refs` are shifted (`shifts`), the rest is listed in `skipped`. Geometric tolerances with datums and toleranced distances are written as semantic PMI. Revisions: `--revise` or the UI option "Update existing model (new revision)" changes only the differences and writes a change list (`changes.json`, `review.md`). The geometry check confirms that a dimension occurs in the solid, not on which feature. |
| A13 | STEP AP242 with semantic PMI and GLB | `cad/export.py` | `test_export` | met | AP242 with semantic PMI: hole diameters with fit or tolerance, toleranced distances between faces, assumed dimensions with a note, geometric tolerances with datums. No graphical PMI presentation. |
| A14 | Upload a drawing, the analysis starts | `web/app.py`, `web/static/app.js` | `test_web` | met | The run starts automatically on drop; the option "Upload only" defers the start. |
| A15 | Check the PMI of the STEP file | `cad/export.py` (`read_pmi`), `cad/checks.py` | `test_export`, `test_checks` | partial | Own read-back with OpenCascade, checked in every export. The NIST STEP File Analyzer was not run. |

In addition, the demo without a language model (`d2c/demo`, `examples/demo`) covers the whole flow (`test_demo`, `test_example`); it produces three parts as STEP without confidential data, the runs on the sample drawings are listed under results. All results open in FreeCAD (`scripts/freecad_check.py`); NX was not checked (limit).

## Results

### Success criteria of the challenge

| Must-have | Result | Method | Evidence |
| --- | --- | --- | --- |
| At least 3 parts as STEP | 21 of 22 drawings exported as STEP AP242 (the one abort is fixed, see `docs/ergebnisse.md`); without confidential data the demo builds 3 parts | agent run per drawing, then count of exported files | table "Result per part" in `docs/ergebnisse.md`; the demo files are produced by `python -m d2c.demo` |
| 100 % valid | 350 of 351 server checks passed (one assumed dimension without PMI, reported in the review list), 21 of 21 STEP files open in FreeCAD and pass the check of solid, size and holes | checks after every build and on export (solid, dimensions, STEP re-import); independent measurement with `scripts/freecad_check.py` | `docs/ergebnisse.md`, section 1; demo: line `3 of 3 parts exported, all checks passed` of `python -m d2c.demo` |
| Review list for assumed dimensions | 39 review items in 21 parts, each marked in the model, with a reason | `submit_spec` rejects an assumed or uncertain dimension without a review item; `export_step` writes `review.md` | `review.md` per part, example `examples/demo/demo_bracket/review.md` |

Valid here means: consistent with the captured specification (see A8). Agreement with the reference models is shown in the following table.

### Key figures

<!-- ZAHLEN -->
Key figures of the last full run with Claude Sonnet through the agent batch, on 22 drawings (21 sample parts and one changed revision, as of 18.10.2026). The drawings and reference models are confidential and not part of the repository.

| Metric | Value |
| --- | --- |
| Drawings | 22 |
| STEP AP242 produced | 21 of 22 |
| Server checks passed | 350 of 351 |
| Open and measure in FreeCAD | 21 of 21 |
| Matching the reference (volume 1 %, bounding box 0.05 mm, same hole diameters) | 10 of 21 |
| Bounding box within 0.05 mm | 20 of 21 |
| Volume within 1 % | 14 of 21 |
| Duration per part (mean) | about 3 minutes |
| Cost per part (mean) | about 0.45 USD |
<!-- /ZAHLEN -->

Context: "Server checks passed" means that the model agrees with the captured specification (see A8), not that it agrees with the drawing. The latter is measured by the comparison with the reference models. The result varies between runs: an earlier full run on 21 drawings (17.10.2026) matched 9 of 20. Local models through Ollama, sample with the demo and three sample parts: qwen 4b fails the demo and builds 1 of 3 parts (776 s), qwen 27b passes the demo and builds 1 of 3 parts, the other two run into the time limit. The comparison is a development tool (`scripts/eval_refs.py`); the server never sees reference data. Method and further figures: `docs/ergebnisse.md` (German).

## Limitations

- **Input:** benchmarked only on vector PDFs. Scans and images are accepted and read visually by the model; vector measurement is not available, a benchmark is missing.
- **Check = consistency:** the checks compare the model with the data the language model captured itself. A misread dimension passes them. Only part of the printed dimensions is bound to the model through `refs`; values without a dimension reference and printed dimensions that do not occur in the solid are listed in the review list ("Values without dimension reference", "Dimensions without model reference"). Holes are checked by diameter and position; whether they go through is reported, depth and side are not compared with the drawing.
- **Reading weaknesses:** draft angles, countersinks, small holes and internal cavities that only appear in sections are missing or deviate; a reference dimension from a centre line is occasionally misread. The result varies from run to run.
- **Models:** local models (Ollama) and OpenRouter are connected. Claude Sonnet is benchmarked; local models are only checked on a small sample (see results), OpenRouter not at all.
- **PMI:** semantic PMI only, no graphical presentation. A dimension or geometric tolerance whose point does not hit a matching face is dropped with a warning. Threads are tap drill holes without thread geometry.
- **CAD systems:** FreeCAD opens all results; NX was not checked. The NIST STEP File Analyzer was not run.
- **Scope:** single parts made of one solid, no assemblies, no freeform surfaces.
- **Operation:** the UI has no login and listens only on `127.0.0.1`. For several users or access over a network, put a reverse proxy with authentication in front of it; open operation on the internet is not intended.

## Data flow and privacy

| Data | Destination |
| --- | --- |
| Images and text layer of the drawing (crops, pages) | to the selected language model: Anthropic for Claude, the provider behind OpenRouter for OpenRouter, your own machine for Ollama |
| Captured specification and tool calls | to the same model, as part of the conversation |
| Model, STEP, review list, run logs | local only, under `results/` |
| CAD server (container) | no network: `--network none` or internal Compose network |
| Reference models | no access by server or model; only `scripts/eval_refs.py` reads them |

The agent does not store conversation history in the Claude Code configuration folder (`no-session-persistence`). Where a company's drawings may go is decided by the choice of model: for confidential drawings a local model (quality not benchmarked) or a Claude account under your organisation's contractual terms.

**Production use:** for continuous operation use an organisation API key (`ANTHROPIC_API_KEY`, Anthropic commercial terms), not the personal Claude Code login. The latter is intended for personal use and suits trying things out with `./start.sh`, not batch runs. Retention and data use follow the terms of the chosen provider and the organisation's settings.

## Configuration

All settings are environment variables, in `.env` (template `.env.example`) or in the shell. They are read on every call, `D2C_LOG` once at process start.

| Variable | Default | Purpose |
| --- | --- | --- |
| `D2C_DATA` | `/data` | drawings folder (in the container); uploads go to `uploads/` |
| `D2C_RESULTS` | `/app/results` | results folder |
| `D2C_TABLES` | `/tables` | optional folder with custom tolerance tables (see below) |
| `D2C_TIMEOUT` | `60` | time limit of one model build in seconds |
| `D2C_LOG` | `info` | log level on stderr: `debug`, `info`, `warning`, `error` |
| `D2C_LLM_MODEL` | empty | model; only a default in the UI, authoritative on the command line |
| `D2C_LLM_BUILD_MODEL` | empty | second model that builds from the specification; the first one only reads the drawing |
| `D2C_LLM_BASE_URL` | empty | Anthropic-compatible endpoint (OpenRouter without `/v1`, Ollama) |
| `D2C_LLM_BUILD_BASE_URL` | empty | endpoint of the build phase if it differs from the read phase ("cloud reads, local builds") |
| `D2C_LLM_CONTEXT`, `D2C_LLM_THINKING` | `64000`, empty | context window and thinking token limit for models without a Claude name on a custom endpoint |
| `D2C_REPORT` | empty | `full`: the build tools reply with the full report instead of the compact one |
| `D2C_LLM_API_KEY` | empty | key for this endpoint |
| `D2C_OLLAMA_URL` | `http://localhost:11434` | Ollama whose models the UI lists (with Compose `http://host.docker.internal:11434`) |
| `D2C_MCP_URL` | `stdio` | agent connection to the CAD server: `stdio`, a URL such as `http://mcp:8765/mcp` or a command line (`docker run -i --rm --network none --read-only --tmpfs /tmp ...`) |
| `D2C_ALLOWED_HOSTS` | empty | comma-separated host names the web interface accepts besides `localhost`, `127.0.0.1` and `[::1]` (needed behind a reverse proxy; other Host headers get HTTP 400) |
| `D2C_MAX_RUNS` | `2` | concurrent runs of the web UI; further requests get 429 |
| `D2C_MAX_USD` | `3` | cost limit per run in USD, `0` = off; `--max-usd` takes precedence |
| `D2C_MAX_MINUTES` | `20` | time limit per run in minutes, `0` = off; `--max-minutes` takes precedence |
| `D2C_REF_DATA` | `/app/data` | reference models for `scripts/eval_refs.py` (development only) |
| `ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN` | empty | access to Claude; without either, the Claude Code login on the machine is used |
| `PORT` | `8080` | port of `./start.sh` |

Four language model variants are commented in `.env.example`: Claude login, Anthropic API key, OpenRouter, Ollama.

**Tolerance tables (`D2C_TABLES`):** without files the built-in tables from `d2c/quality/iso_tables.py` apply: ISO 2768-1 (f, m, c, v) and ISO 286-1 (H6 to H11, h6 to h11, g6, js6, k6, m6, n6, p6, r6, up to 500 mm). A file `*isolim*.xml` (ISO 286) or `*toltabs*.xml` (ISO 2768) in the `D2C_TABLES` folder replaces the respective table entirely. `start.sh` and Compose mount `tables/` from the repository root to `/tables`; small excerpts in XML format are in `d2c/demo/tables`. A fit that is neither in the built-in nor in the custom tables (for example E8 or H12) produces an export warning, and the dimension counts as unchecked. The tolerance table of the review list shows limits and deviations.

## Troubleshooting

Every run writes its event log as JSONL to `results/<part>/runs/`, from the UI as from the command line. The first line is `meta` (run_id, part, drawing, model, start time), every further line is an event with `at` (seconds since start). If the last line `done` is missing, the run was aborted. The "Runs" tab of the UI shows the logs and replays a run with the recorded timing.

Server and web app logs go to stderr; `D2C_LOG=debug` also shows every worker call with duration and return value. After a successful export `results/<part>/report.json` lists every check with expected and actual value; `review.md` explains the assumptions and the marked faces. If an export fails, the check lines are in the run's event log.

## Verifying results

Without a model session, part by part:

```
uv run python scripts/replay.py            # rebuild and check ops from results/, write nothing
uv run python scripts/replay.py --export   # also rewrite STEP, report and review list
D2C_RESULTS=results uv run python scripts/eval_refs.py   # compare exported models with reference STEP (EVAL.md, EVAL.json)
python scripts/freecad_check.py results/<part>/<part>.step --spec results/<part>/spec.json
python scripts/freecad_check.py results/<part>/<part>_mid.step --spec results/<part>/spec.json --mid --tol 0.01
uv run python scripts/check_example.py     # check examples/demo against a fresh demo run
python scripts/bench_models.py --models sonnet <ollama-model> --parts demo --mode full \
    --drawings <drawings> --results <results> --out <folder>
```

- `replay.py` optionally takes part names (default: every folder with `ops.json`) and exits with code 1 if a part is not ok. With `--export` it names a finding of the export gates (see specification 6.6) and then exports anyway.
- `eval_refs.py` reads the references from `D2C_REF_DATA` (default `/app/data`) and serves development and quality assurance; the server never sees them.
- `freecad_check.py` opens the STEP file in FreeCAD (`freecadcmd`, not in the image) and measures solid, size and holes independently of the build. `--tol` sets the threshold in mm (default 0.01). `--mid` checks a tolerance-midpoint STEP file against the measured values from `report_mid.json`. Exit code 0 if the check passes, 1 on an error, 2 without FreeCAD.
- `check_example.py` rebuilds the demo and compares volume and bounding box of all three parts with the `report.json` files under `examples/demo/`; `--update` rewrites them.
- `bench_models.py` benchmarks models, parts and modes (`full`, `build`, `hybrid`); `--drawings` and `--results` are required. Method in `docs/ergebnisse.md`.

## Project layout

```
d2c/                   package, organised in layers (rule: docs/spec.md, section 4.4)
  config.py            environment variables D2C_*
  spec.py              data model PartSpec and acceptance thresholds
  session.py           session: parts, build, review list, export, previous versions, persistence
  log.py, oppath.py, errors.py   logging, operation paths, readable error messages
  drawing/             reading the drawing: pages (pages.py), text layer (text.py), scale (scale.py), vector geometry (vector/)
  cad/                 geometry with CadQuery: operations, checks, STEP export, sandbox, worker
  quality/             tolerances (tolerances.py, iso_tables.py), plausibility check (crosscheck.py), review list (review.py)
  server/              MCP server: tools (tools.py), input layer (coerce.py), hints (guide.py), instructions
  agent/               agent on the Claude Agent SDK: run (runner.py), model backend, event log (trace.py), batch run (batch.py)
  web/                 web UI (Starlette) with static files
  demo/                demo without language model including demo data (drawing, specification, operations, tables)
tests/                 tests (pytest), grouped by topic; test_layers.py checks the layer rule
scripts/               make_demo_drawing.py, replay.py, eval_refs.py, freecad_check.py, bench_models.py, check_example.py
examples/demo/         sample result of the demo (STEP, GLB, review list, reports)
docs/                  specification, architecture, results, UI design
Dockerfile, requirements.lock          reproducible image: dependencies from the lock file, base image by digest
docker-compose.yml, start.sh, .env.example   running the web UI
LICENSE, NOTICE        license of the project and the third-party components
```

The layers and their allowed imports are listed as a table in `docs/spec.md`, section 4.4; `tests/test_layers.py` checks them. Tests with the marker `refdata` need reference data under `/app/data` and are skipped otherwise; local tests of this kind belong in `tests/test_refdata.py` (not versioned).

Development: `uv sync --extra dev`, then `uv run ruff check`, `uv run ruff format --check` and `uv run pytest`. CI (`.github/workflows/ci.yml`) checks Ruff and formatting, compares `requirements.lock` with `uv.lock`, builds the image on amd64 and arm64, runs the tests and the demo with `--network none` and compares the sample result.

## Licenses

Drawing2CAD is licensed under the [GNU Affero General Public License, version 3 or later](LICENSE) (`AGPL-3.0-or-later`). The reason is PyMuPDF (AGPL-3.0 or commercial license from Artifex), which runs in the same process: anyone offering the web UI to third parties over a network must offer them the source. Distribution under other terms would require a commercial PyMuPDF license or replacing PyMuPDF.

Third-party components and their licenses, with versions from `pyproject.toml` and `uv.lock`, are listed in [NOTICE](NOTICE): CadQuery 2.8.0 (Apache-2.0), OCP 7.9.3 and Open CASCADE (LGPL-2.1 with exception), PyMuPDF 1.28.2 (AGPL-3.0), mcp 2.3.0 and pydantic (MIT), claude-agent-sdk 0.2.168 (MIT), model-viewer (Apache-2.0), Inter Tight and JetBrains Mono (SIL OFL 1.1), poppler-utils in the image (GPL).
