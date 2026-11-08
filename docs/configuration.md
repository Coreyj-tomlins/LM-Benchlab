# Installation and configuration

[← Back to LM Benchlab](../README.md)

## Docker

Install Docker with the Compose plugin. From the project folder:

```sh
docker compose up -d --build
```

Open **http://127.0.0.1:8768**. API keys are optional for inference servers that allow unauthenticated local access. The dashboard has no authentication; keep it bound to localhost.

For custom settings, copy [`.env.example`](../.env.example) to `.env` in a fresh checkout. Keep an existing `.env` when upgrading. Compose reads this file automatically; native Python does not.

| Compose variable | Default | Purpose |
|---|---|---|
| `LM_BENCHLAB_PORT` | `8768` | Dashboard port on your computer. The published address stays `127.0.0.1`; the container port stays `8768`. |
| `LM_BENCHLAB_DEFAULT_ENGINE_HOST` | `host.docker.internal` | Initial inference hostname or IPv4 address as seen from the container. |
| `LM_BENCHLAB_MASTER_KEY` | Empty | Optional Fernet key for encrypted engine-key storage. |

For example, `LM_BENCHLAB_PORT=8769` publishes the dashboard at `http://127.0.0.1:8769`. Apply changes with `docker compose up -d`.

## Native Python

Use Python **3.10 or later**. No frontend build step is required.

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

Open **http://127.0.0.1:8768**. Native runs use `127.0.0.1` as the default inference host and save history in `data/` beside the application.

Set overrides in the same shell session before starting the app. To use another dashboard port:

```powershell
# Windows PowerShell
$env:LM_BENCHLAB_PORT = '8769'
.\.venv\Scripts\python app.py
```

```sh
# macOS / Linux
LM_BENCHLAB_PORT=8769 .venv/bin/python app.py
```

## Connect an inference server

1. Start the server's OpenAI-compatible API and load a model.
2. Select LM Studio, vLLM or add a custom engine profile.
3. Enter a host and port, or a full HTTP(S) base URL such as `https://inference.example.com/v1`.
4. Fetch models, choose a model and test the connection before starting a benchmark.

Typical local ports are `1234` for LM Studio and `8000` for vLLM. The host must be reachable **from the Python server or Docker container**, rather than only from your browser.

| Where LM Benchlab runs | Inference server location | Host to use |
|---|---|---|
| Native Python | Same computer | `127.0.0.1` |
| Docker Desktop | Docker host computer | `host.docker.internal` |
| Docker | Another container on the same network | The inference container's hostname |
| Either | Remote server | Its reachable hostname or full HTTP(S) base URL |

Full URLs must not contain credentials, query parameters or fragments. `LM_BENCHLAB_DEFAULT_ENGINE_HOST` changes the initial hostname or IPv4 address for new engine and judge configurations; saved browser profiles keep their existing values. Configure full URLs through the custom engine form.

Only configure endpoints you trust: they receive the prompts for a run. External providers may charge for benchmark and judge requests. If enabling LAN access on an inference server, restrict it to trusted clients with firewall rules. HTTPS is supported for remote endpoints.

### Linux Docker host networking

On Linux Docker Engine, `host.docker.internal` may need an explicit host-gateway mapping. Create a local `compose.override.yaml` beside `compose.yaml`:

```yaml
services:
  lab:
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

Compose loads this override automatically. Docker Desktop already provides its own host mapping.

To connect an existing vLLM container to the project's inference network:

```sh
docker network connect llm-lab-net vllm
```

The network name is retained for compatibility with existing installations.

## Encrypted engine-key storage

Saved engine API keys are encrypted in SQLite with a Fernet master key. To generate one for Docker:

```sh
docker compose build
docker compose run --rm --no-deps lab python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

If `.env` does not exist, copy `.env.example` to `.env`. Set `LM_BENCHLAB_MASTER_KEY` to the generated value without replacing other existing settings, then run `docker compose up -d`.

For native Python, generate a key with the virtual environment's Python using the same `-c` expression and set `LM_BENCHLAB_MASTER_KEY` in your shell before starting the app. Native Python does **not** automatically load `.env`.

Keep the master key private, backed up and unchanged across restarts. Generating a replacement on every startup makes existing saved engine keys unreadable. Never commit `.env` or share the generated key.

Judge credentials and workspace preferences are stored in browser local storage. Prompts, responses, reasoning and reviews are stored in an unencrypted database. Use synthetic evidence and a private browser profile; check exports before sharing. See [SECURITY.md](../SECURITY.md).

## Runtime environment reference

| Variable | Native default | Purpose |
|---|---|---|
| `LM_BENCHLAB_HOST` | `127.0.0.1` | Dashboard bind address. Docker sets `0.0.0.0` inside the container and publishes it only to host loopback. |
| `LM_BENCHLAB_PORT` | `8768` | Native listening port. In Compose, this setting controls the published host port; the container keeps port `8768`. |
| `LM_BENCHLAB_DB` | `data/lm_benchlab_history.sqlite3` beside the app | SQLite file path. Docker's default resolves into `/data/`. Existing default history files are reused; see [upgrades](operations.md#upgrades-and-rename-compatibility). |
| `LM_BENCHLAB_MASTER_KEY` | Empty | Fernet key required for encrypted engine-key storage. |
| `LM_BENCHLAB_NO_BROWSER` | `1` | Set to `0` to open a browser on native startup. |
| `LM_BENCHLAB_DEFAULT_ENGINE_HOST` | `127.0.0.1` | Initial inference hostname or IPv4 address. Docker supplies `host.docker.internal`. |

The app validates runtime settings at startup. Relative database paths resolve beside the application, independent of the working directory. Runtime defaults, application name and version are defined in `config.py`; `/api/config` exposes only the public name, version and default inference host.
