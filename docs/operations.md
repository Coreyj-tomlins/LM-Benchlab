# History, backups and upgrades

[← Back to LM Benchlab](../README.md) · [Installation and configuration](configuration.md)

## Saved runs and reviews

A run saves automatically when it completes. Active progress is held in memory, so stopping the app loses an unfinished run.

In **History**, select a run and request to inspect the original prompts, final response, returned reasoning, errors, token metrics and review status. Mark answers **Correct**, **Incorrect** or **Unreviewed**, and export a selected run as CSV. The **Compare** screen displays saved-run metrics without modifying them.

An API success means a response arrived; it does not establish that the answer is correct. Manual reviews and optional LLM judge scores help assess quality. A judge can make mistakes and is not ground truth.

Compare equivalent models, quantization, prompt types and generation settings when evaluating inference engines. **Max in-flight** limits simultaneous HTTP requests; sequential suites use one request at a time. Start small and tune concurrency, output limits and timeouts to your hardware. The inference server can impose its own concurrency limits.

## Storage

The Compose project is `lm-benchlab`, the service is `lab` and the local image is `lm-benchlab:local`. Use `docker compose logs lab` and `docker compose exec lab` rather than relying on generated container names.

For compatibility, the `history` volume explicitly uses the Docker volume `llm-lab_history`, and the inference network remains `llm-lab-net`. Fresh installs use these resource names too. Docker maps `/app/data` to `/data` so the default database stays in the persistent volume.

New default databases are named `lm_benchlab_history.sqlite3`. If that file is absent but the previous `llm_concurrency_history.sqlite3` exists in the default data directory, the app reuses it. It does not rename, copy or erase the file. An explicitly configured database path takes priority; if both default files exist, the new filename takes priority.

The database contains unencrypted prompts, responses and endpoint details. Keep it private and use synthetic inputs. See [SECURITY.md](../SECURITY.md).

## Back up history

Stop the service before copying the database:

```sh
docker compose stop lab
docker run --rm -v llm-lab_history:/source:ro -v "${PWD}:/backup" alpine sh -c 'cd /source && tar -czf /backup/lm-benchlab-history-backup.tar.gz .'
```

On PowerShell, use an absolute folder path if Docker cannot resolve `${PWD}`. Verify the archive exists and store it privately. Native installations can copy the SQLite database while the app is stopped. Back up the master key separately if using encrypted engine keys.

Restart after a backup with:

```sh
docker compose start lab
```

For the first upgrade from the legacy project, substitute `docker compose -p llm-lab stop lab` for the stop command. Use the same `-p llm-lab` option to restart that legacy installation if you are only making a backup.

## Upgrades and rename compatibility

1. Stop the running service and back up history as described above. The old `llm-lab` project must be stopped before the new project starts so it releases the dashboard port.
2. Replace application files in the same project folder. Keep the existing `.env`, master key and history volume.
3. Run `docker compose up -d --build` to start `lm-benchlab`.
4. Refresh the browser and confirm saved history is available. Update scripts that depend on the old project or fixed container name to use the new Compose project.

Older `APP_HOST`, `APP_PORT` and `LLM_LAB_*` environment variables remain accepted as fallbacks when the canonical `LM_BENCHLAB_*` setting is unset. Compose also accepts the previous `MODELRIG_PORT` host-port setting. Use canonical names for new configuration.

Browser settings, saved engine profiles and theme preferences migrate from the legacy storage keys on first load. Existing customized system prompts are retained. Older runs may lack newly added category metadata; the legacy category ID `Interface` still represents Networking.

**`docker compose down -v` deletes saved history.** Keep a verified backup before changing volumes or storage paths.

## Troubleshooting

```sh
docker compose ps
docker compose logs -f lab
curl http://127.0.0.1:8768/api/history
```

| Symptom | What to check |
|---|---|
| Models cannot be fetched | The server's `/v1/models` endpoint, loaded models, credentials and firewall rules. Test reachability from the Python server or container. See [network configuration](configuration.md#connect-an-inference-server). |
| API key cannot be saved | Install `requirements.txt` and configure a valid, stable `LM_BENCHLAB_MASTER_KEY`. |
| Old interface after updating | Rebuild the image, confirm the dashboard port and hard-refresh the browser. |
| Timeouts or memory pressure | Lower maximum in-flight requests or output tokens; increase timeout if appropriate. Check model-server concurrency limits. |
| Port already in use after the rename | Stop the previous project with `docker compose -p llm-lab stop lab` before starting `lm-benchlab`, or choose another published port. |

Adjust the example URL if you changed `LM_BENCHLAB_PORT`.
