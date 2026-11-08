# LM Benchlab

LM Benchlab is a self-hosted benchmark dashboard for LM Studio, vLLM and other OpenAI-compatible inference servers. Compare throughput, latency and response quality using synthetic prompts, save runs locally and review individual answers.

The Python backend serves a standalone browser interface. No Node.js build step, CDN or hosted account is required.

## Features

- Choose the prompt types that fit your use case: **Networking, SIEM, Chat, Sales, Ticket support, Coding, Writing, Data analysis and Summarization**.
- Enable or disable each type with checkboxes in **Settings** and edit its system prompt. For example, enable only Chat and Sales to exclude networking and SIEM cases.
- Run mixed load tests, deeper reasoning tasks, a sequential Python engineering mission or a multi-turn conversation benchmark.
- Save results and prompts in SQLite, mark answers correct or incorrect, compare runs and export CSV.
- Connect multiple inference engines, optionally store encrypted engine API keys and use an optional LLM judge.
- Switch between light and dark themes. Workspace preferences are stored in your current browser.

## Quick start with Docker

Install Docker with the Compose plugin. From the project folder:

```sh
docker compose up -d --build
```

Open **http://127.0.0.1:8768**. The dashboard is bound to localhost. It is a local tool without authentication; keep it off the public internet.

API keys are optional for inference servers that allow unauthenticated local access. To save an engine API key, configure the encryption key described below.

For a different dashboard port or default inference host, copy `.env.example` to `.env` in a fresh checkout and edit its documented settings before starting. Keep an existing `.env` when upgrading. For example, `LM_BENCHLAB_PORT=8769` publishes the dashboard at `http://127.0.0.1:8769`.

## Run directly with Python

Use Python **3.10 or later**. Create a virtual environment and install the runtime dependency.

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python app.py
```

macOS or Linux:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py
```

Open **http://127.0.0.1:8768**. Native runs default to a database in `data/` beside the application and use `127.0.0.1` as the default inference host. Native Python reads environment variables from your shell; it does not automatically load `.env`.

## Connect an inference server

1. Start the server's OpenAI-compatible API and load a model.
2. Select LM Studio, vLLM or add a custom engine profile.
3. Enter a host and port, or a full HTTP(S) base URL such as `https://inference.example.com/v1`. Full URLs must not contain credentials, query parameters or fragments.
4. Fetch models, select a model and test the connection before starting a benchmark.

Typical local ports are `1234` for LM Studio and `8000` for vLLM. Native runs default to `127.0.0.1`; Docker runs default to `host.docker.internal`, which addresses the host computer on Docker Desktop. For an inference container on the same Docker network, use its container hostname. Set `LM_BENCHLAB_DEFAULT_ENGINE_HOST` to change the initial host for new engine and judge configurations; saved browser profiles keep their existing values. Use a hostname or IPv4 address for this default. Configure full HTTP(S) URLs through the custom engine form.

On Linux Docker Engine, `host.docker.internal` may need an explicit host-gateway mapping. Create a local `compose.override.yaml` beside `compose.yaml` with:

```yaml
services:
  lab:
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

Compose loads this override automatically. Use it when needed on Linux; Docker Desktop already provides its own host mapping.

To connect a vLLM container to this project's network:

```sh
docker network connect llm-lab-net vllm
```

The inference server must listen on an address reachable from LM Benchlab. If enabling LAN access, limit it to trusted clients with firewall rules. HTTPS is supported for remote endpoints. Only configure endpoints you trust: benchmark prompts are sent to those servers, and external providers may charge for requests.

## Choose prompt types

Open **Settings**, tick the types to include, adjust any system instructions and click **Save settings**. At least one type must be enabled. All nine types are enabled initially; existing customized system prompts are retained when upgrading.

The selection applies to **Mixed stress test** and **Deep reasoning**. Cases are distributed across enabled types in a deterministic cycle. A run shorter than the number of enabled types cannot cover them all. Saved runs include the selected categories and actual prompts, so you can inspect what was tested after changing Settings.

The **Agentic Python** and **Conversation speed** suites use fixed, linked scenarios. Category checkboxes do not alter those sequential suites. The legacy API category ID `Interface` continues to represent Networking so older saved runs remain readable.

| Suite | Requests | Behavior |
|---|---:|---|
| Mixed stress | 1–500 | Independent scenarios from enabled prompt types; control total requests and maximum in flight. |
| Deep reasoning | 20 | More demanding tasks from enabled types, with review checklists. Cases cycle when fewer than 20 distinct cases are available. |
| Agentic Python | 12 | Sequential phases of one synthetic `eventledger` project, carrying prior output forward. |
| Conversation speed | 20 | Sequential messages in one synthetic support conversation, carrying the transcript forward. |

**Max in-flight** controls simultaneous HTTP requests. Sequential suites use one request at a time. Start with a small run and adjust concurrency, output limits and timeout for your hardware. Compare equivalent models, quantization, enabled categories and generation settings when evaluating inference engines.

An API success means a response arrived; it does not establish that the answer is correct. Manual reviews and optional judge scores help assess quality. A judge may make mistakes and is not ground truth.

## Credentials and configuration

Docker Compose reads `.env` beside `compose.yaml`. `.env.example` lists the supported Compose settings with safe defaults:

| Compose variable | Default | Purpose |
|---|---|---|
| `LM_BENCHLAB_PORT` | `8768` | Dashboard port on your computer. The published address stays `127.0.0.1`; the container port stays `8768`. |
| `LM_BENCHLAB_DEFAULT_ENGINE_HOST` | `host.docker.internal` | Initial inference hostname or IPv4 address as seen from the container. |
| `LM_BENCHLAB_MASTER_KEY` | Empty | Optional Fernet key for encrypted engine-key storage. |

Apply changes with `docker compose up -d`. The app validates its runtime configuration at startup and reports invalid values. For native Python, `LM_BENCHLAB_PORT` sets the server's listening port; in Compose, it changes the published host port while the container continues to listen on `8768`.

Engine API keys are encrypted in SQLite using a Fernet master key. To configure Docker key storage:

```sh
docker compose build
docker compose run --rm --no-deps lab python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

If `.env` does not exist yet, copy `.env.example` to `.env`. Set `LM_BENCHLAB_MASTER_KEY` to the generated value without replacing any other existing settings. Do not share the output or commit `.env`. Then run `docker compose up -d` to apply the configuration. Keep the master key backed up privately and unchanged; replacing it makes saved engine keys unreadable.

For native Python, set overrides in the same shell session before starting the application. For example, to change its dashboard port:

Windows PowerShell:

```powershell
$env:LM_BENCHLAB_PORT = '8769'
.\.venv\Scripts\python app.py
```

macOS or Linux:

```sh
LM_BENCHLAB_PORT=8769 .venv/bin/python app.py
```

To enable native encrypted key storage, generate a key with your virtual environment's Python using the same `-c` expression and set `LM_BENCHLAB_MASTER_KEY` in that shell. Keep the generated key for later sessions and back it up privately; do not generate a replacement on every startup.

| Environment variable | Native default | Purpose |
|---|---|---|
| `LM_BENCHLAB_HOST` | `127.0.0.1` | Dashboard bind address. Docker sets `0.0.0.0` inside the container and publishes it only to host loopback. |
| `LM_BENCHLAB_PORT` | `8768` | Dashboard port. |
| `LM_BENCHLAB_DB` | `data/lm_benchlab_history.sqlite3` beside the app | SQLite path. Docker uses `/data/lm_benchlab_history.sqlite3`. |
| `LM_BENCHLAB_MASTER_KEY` | Empty | Fernet key required for encrypted engine-key storage. |
| `LM_BENCHLAB_NO_BROWSER` | `1` | Set to `0` to open a browser on native startup. |
| `LM_BENCHLAB_DEFAULT_ENGINE_HOST` | `127.0.0.1` | Initial inference hostname or IPv4 address. Docker supplies `host.docker.internal`. |

Relative database paths are resolved beside the application, independent of the working directory. Keep native bind addresses local. Runtime defaults, the application name and version are defined in `config.py`; the frontend reads the public name, version and inference-host defaults from `/api/config`.

Judge credentials are saved in browser local storage. Run prompts, responses, reasoning, reviews and endpoint details are saved in an unencrypted database. Use synthetic evidence and a private browser profile, and check exports before sharing. See [SECURITY.md](SECURITY.md).

## History, backups and upgrades

A completed run is saved automatically. Active progress is held in memory, so stopping the app during a run loses that unfinished run. In **History**, select a run and request to inspect prompts, output, errors, token metrics and review status. CSV exports include the run's requests.

The Compose project is `lm-benchlab`, the service is `lab` and the local image is `lm-benchlab:local`. Container names are generated by Compose; use `docker compose logs lab` or `docker compose exec lab` instead of depending on a fixed container name.

For upgrade compatibility, the `history` volume explicitly uses the existing `llm-lab_history` Docker volume, and the inference network remains `llm-lab-net`. These legacy resource names keep saved results and connected inference containers available across the rename. Fresh installs use the same resource names.

New databases are named `lm_benchlab_history.sqlite3`. If that file is absent but the previous `llm_concurrency_history.sqlite3` exists in the default data directory, the app reuses the existing file. It does not rename, copy or erase it. An explicitly configured database path takes priority.

Older `APP_HOST`, `APP_PORT` and `LLM_LAB_*` environment variables remain accepted as fallbacks when their canonical `LM_BENCHLAB_*` setting is unset. Compose also accepts the old `MODELRIG_PORT` host-port setting. Keep an existing `.env` and master key during upgrades; use the canonical names for new configuration. Browser settings, saved engine profiles and theme preferences migrate from the legacy storage keys on first load.

Before an upgrade, stop the service and make a database backup. For the first upgrade from the old Compose project, use `docker compose -p llm-lab stop lab` instead of the first command below so the old dashboard releases its port:

```sh
docker compose stop lab
docker run --rm -v llm-lab_history:/source:ro -v "${PWD}:/backup" alpine sh -c 'cd /source && tar -czf /backup/lm-benchlab-history-backup.tar.gz .'
```

On PowerShell, use an absolute folder path if Docker cannot resolve `${PWD}`. Store the archive privately and verify it exists. Native installations can copy the SQLite database while the app is stopped. Back up the master key separately if using saved engine keys.

Replace the application files in the same project folder and run `docker compose up -d --build` to start the renamed project. For a backup without an upgrade, restart the stopped service with `docker compose start lab` (or the same `-p llm-lab` option for a legacy installation). Existing runs remain available; old runs may lack newly added category metadata. Update scripts that depend on the old project or fixed container name to use the new Compose project. **`docker compose down -v` deletes saved history.**

## Development and checks

```sh
python -m unittest discover -s tests -v
python -m py_compile app.py config.py complex_cases.py prompt_catalog.py
node tests/check_frontend.mjs
node --test tests/test_frontend.mjs
docker compose config --quiet
```

The frontend checks use Node.js 22 or later. GitHub Actions runs Python tests on Windows and Linux, checks frontend syntax and settings behavior on Linux and builds the Docker image. Dependency update checks cover Python, Docker and GitHub Actions. See [CONTRIBUTING.md](CONTRIBUTING.md).

Before publishing your own copy, review staged files and Git history for private data. `.gitignore` excludes local credentials, databases and editor settings; `.dockerignore` limits the build context to application files. A software license still needs to be chosen by the project owner before this repository is offered as open source.

## Troubleshooting

```sh
docker compose ps
docker compose logs -f lab
curl http://127.0.0.1:8768/api/history
```

- **Cannot fetch models:** check the configured endpoint's `/v1/models` route from the machine or container running LM Benchlab, model availability, credentials and firewall rules.
- **Cannot save an API key:** install `requirements.txt` and configure a valid `LM_BENCHLAB_MASTER_KEY`. Keep the key stable across restarts.
- **Old interface after updating:** rebuild the image, confirm port `8768`, then hard-refresh the browser.
- **Timeouts or memory pressure:** lower maximum in-flight requests or output tokens and increase the timeout if needed. The model server may impose its own concurrency limits.
