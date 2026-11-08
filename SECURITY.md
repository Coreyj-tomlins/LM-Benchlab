# Security

LM Benchlab is a single-user local benchmark tool without user authentication. Keep its dashboard and API bound to localhost. Docker Compose publishes its dashboard only on `127.0.0.1`, using port 8768 by default; changing `LM_BENCHLAB_PORT` changes the port while preserving that local binding. The Python server also defaults to localhost when started directly.

## Data and credentials

- Prompts, responses, reasoning, endpoint addresses, model names and reviews are saved in an unencrypted SQLite database. Use synthetic inputs and check exports before sharing them.
- Inference and judge endpoints receive the prompts needed for a run. Only configure servers you trust. External API providers may charge for benchmark and judge requests.
- Saved engine API keys are encrypted with `LM_BENCHLAB_MASTER_KEY`. Protect that key and the database together, keep the key out of Git, and preserve it across upgrades. Encryption of engine keys does not encrypt the rest of the database.
- Judge credentials and workspace preferences are saved in browser local storage. Use a private browser profile and avoid shared computers for credentials.
- HTTP endpoints send data without transport encryption. Use HTTPS for remote inference servers, or a trusted private connection.
- Do not commit `.env`, databases, exports, credentials or real customer data. Ignore rules are a safeguard; review staged files before publishing.
- `.env.example` contains safe defaults for Docker Compose. Native Python reads shell environment variables instead of automatically loading `.env`. The public `/api/config` response contains only the application name, version and default inference host.

## Reporting a vulnerability

Use the repository's **Security → Report a vulnerability** option when private vulnerability reporting is available. If it is unavailable, open an issue asking for a private contact without including vulnerability details, credentials, customer data or an exploit. Share sensitive details only through an agreed private channel.

Security fixes target the current source tree. Back up saved results before updating and follow the setup instructions in [README.md](README.md).
