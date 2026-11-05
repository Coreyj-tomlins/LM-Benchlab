# ModelRig v7.3

ModelRig is a self-hosted, Dockerised browser application for benchmarking LM Studio, vLLM and other OpenAI-compatible inference servers.

## What's new in v7.3

- Renamed the product to **ModelRig** throughout the visible interface, page title and application startup banner.
- Added a compact custom **M** monogram in the sidebar and a matching browser favicon. The vector mark is also included as `modelrig-mark.svg` for future reuse.
- Updated the sidebar version indicator to **VERSION 7.2**.
- Kept the benchmark API, Docker service/container identity and SQLite storage unchanged for safe upgrades. The Compose image is tagged `modelrig:7.2`.
- No changes to test logic, 500-request limit, 20-task reasoning suite, history, comparisons or light/dark preference.

## Features retained from v7.1

- Removed the standalone **Results by workload** table from the Benchmark home page; workload distribution remains available in the monitor column.
- Widened the content layout to use the available browser width.
- Reworked the sidebar service/footer information so the Docker, SQLite, API and version labels do not crowd each other.
- Added a light/dark toggle in the top bar. The selection persists in the browser using localStorage, including after page reloads.
- Benchmark engine, History, manual reviews, API contract, Compose project and named Docker volume are unchanged.
- Add multiple local engine profiles, including optional bearer API keys, from the benchmark setup screen.
- Edit system prompts and workspace version labels from Settings. An optional LLM judge can score successful responses from 0 to 5 and save its rationale with the run.

## Original v7 features

- Redesigned, responsive interface: restrained sidebar, clean typography, structured setup and live execution monitor.
- Searchable History with engine and test-suite filters; select any saved run to inspect requests, complete prompts, responses, reasoning and errors.
- New **Compare** screen for comparing two saved runs' throughput, latency, completion and output volume.
- Same benchmark engine, OpenAI-compatible endpoints and SQLite database as v6; no changes to the existing database tables.
- No Node.js or frontend build step required. The frontend is self-contained and served by Python.

## Quick start (Windows, macOS, Linux)

From the folder containing `compose.yaml`:

```sh
docker compose up -d --build
```

Open **http://127.0.0.1:8768** on the computer running Docker.

Docker Desktop is recommended on Windows and macOS. The dashboard is bound to the host loopback interface by default and is **not** an internet-facing service.

## Upgrade from v7.1 (or earlier) without losing history

1. Back up the SQLite Docker volume if it contains important results (see below).
2. Extract the v7.3 files **into the same folder as the existing `compose.yaml`**, replacing application files.
3. Run `docker compose up -d --build` from that folder.
4. Refresh the browser at http://127.0.0.1:8768; the sidebar should show **VERSION 7.2**.

The Compose project name remains `llm-lab`, the service remains `lab`, and the named volume remains `history`. Existing results and manual reviews remain available as long as you use the same Docker volume.

**Do not run** `docker compose down -v`, `docker volume prune`, or delete the history volume unless you intend to erase results.

## Connect an inference server

### LM Studio on the same Windows/macOS host

- Start its OpenAI-compatible API on port `1234`.
- In the app select **LM Studio**, host `host.docker.internal`, port `1234`.
- Depending on your configuration, LM Studio may need to listen on a non-loopback interface for Docker Desktop to reach it. Enable LAN serving **only on trusted networks** and enforce host firewall rules.

### vLLM in a Docker container

Make sure the vLLM container and the lab container share a Docker network:

```sh
docker compose up -d --build
docker network connect llm-lab-net vllm
```

Run `docker network inspect llm-lab-net` to confirm both containers appear. Docker will return an error if `vllm` is already connected; that is harmless.

In the dashboard choose **vLLM**, host `host.docker.internal`, port `8000`. This is the default for a vLLM server running on the Docker host. If vLLM runs in another container, use its Docker-network hostname instead.

Custom engines may also use a complete OpenAI-compatible base URL, such as `https://10.10.30.100/v1`. Leave the port blank when the URL includes its scheme and path. The endpoint must use `http://` or `https://` and must not include credentials, query parameters or fragments.

If running vLLM elsewhere, set the hostname reachable **from within the lab container**, not from your browser.

## Benchmarks

**Mixed stress test:** 1–500 requests using network-interface, SIEM, IT-support/chat and MSSP sales scenarios.

**Deep reasoning:** exactly 20 more demanding multi-step tasks (five in each category). The app includes review checklists with each case; these are for human evaluation, not automatic grading.

**Agentic Python mission:** exactly 12 sequential phases for one synthetic `eventledger` Python project. The model receives the prior phase output before each next phase and is asked to move from requirements and design through implementation, testing, debugging, security review and maintainer handoff. This measures state retention, tool-like planning, self-correction and software-engineering judgment rather than independent prompt quality.

**Conversation speed test:** exactly 20 sequential support messages in one synthetic IT helpdesk conversation. Each response receives the accumulated transcript, allowing you to measure per-message latency alongside context retention, follow-up quality and troubleshooting continuity.

**Total requests** sets how many cases to submit. **Max in-flight** sets how many HTTP requests can be active at once (the rest are queued within the lab). vLLM may additionally limit simultaneous GPU sequences with `--max-num-seqs`. Increasing accepted requests doesn't guarantee greater model throughput.

Recommended starting point for a 10GB RTX 3080 and Qwen3-4B-Thinking-2507:

| Suite | Requests | Max in-flight | Output limit | Timeout |
|---|---:|---:|---:|---:|
| Mixed stress | 100 | 16–32 | 1536 | 900 s |
| Deep reasoning | 20 | 4 | 4096 | 1200 s |
| Agentic Python | 12 | 1 | 4096 | 1200 s |
| Conversation speed | 20 | 1 | 1024 | 900 s |

These are starting values, not performance guarantees. Run one inference server at a time on a single GPU, keeping quantisation/model/context settings comparable.

## Saved history and correctness reviews

- A run saves when the batch completes; during the test, progress is held in server memory.
- History stores individual system and user prompts, final response, any returned reasoning, error text, reported token metrics, status and reviewer verdict.
- **API success** only means a response was returned. Correct/incorrect counts are **manual reviews**.
- Click a saved run and then a request to inspect its conversation and mark Correct / Incorrect / Unreviewed.
- Export the selected run as CSV.
- The Compare screen displays existing saved-run metrics; it does not modify them.
- LLM judge keys are stored in browser local storage and sent only to the configured endpoint. Do not use this feature on a shared browser profile or store production secrets in the repository.
- Engine API keys are encrypted at rest by the backend with `LLM_LAB_MASTER_KEY`; the plaintext key is not stored in SQLite. Copy `.env.example` to `.env`, generate a Fernet key with the documented command, and keep that master key backed up and unchanged.
- The judge is an assistant for review, not ground truth. Inspect its score and rationale alongside manual review.

SQLite is stored unencrypted in Docker volume `llm-lab_history` (Compose volume `history`). **Use synthetic benchmark evidence and avoid customer secrets**. This is a local test tool without authentication; do not expose port 8768 outside localhost.

## Back up your database (optional)

A simple named-volume backup while the lab is stopped:

```sh
docker compose stop lab
docker run --rm -v llm-lab_history:/source:ro -v "${PWD}:/backup" alpine sh -c 'cd /source && tar -czf /backup/llm-lab-history-backup.tar.gz .'
docker compose start lab
```

On Windows PowerShell, Docker usually accepts `${PWD}` as a path; if not, use an absolute folder path. Check that the archive was created before deleting or moving any data.

## Troubleshooting

```sh
docker compose ps
docker compose logs -f lab
curl http://127.0.0.1:8768/api/history
```

If the v7.3 UI does not appear, hard refresh your browser (Ctrl+Shift+R on Windows; Cmd+Shift+R on macOS) and ensure you are visiting port **8768** rather than an older standalone instance.

If fetching models fails, verify container-to-engine networking and the server's `/v1/models` endpoint. The app only supports unencrypted HTTP on hosts specified in the form. It is intended for trusted local environments.

## Implementation

- Backend: Python standard library (`app.py`) and synthetic task catalog (`complex_cases.py`)
- Frontend: standalone `index.html` with embedded CSS and JavaScript; no CDNs or external dependencies
- Runtime: `python:3.12-slim`, unprivileged container user, all Linux capabilities dropped
- Database: SQLite in persistent Docker named volume
- Endpoint: `127.0.0.1:8768` on the Docker host

The benchmark backend and database schema are preserved from v6; v7.3 retains the existing suites and adds the sequential Agentic Python mission. The Compose project remains `llm-lab` so existing volumes and Docker-network integrations are not disrupted.
