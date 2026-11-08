# Contributing

Use Python 3.10 or later and install `requirements.txt` in a virtual environment. Run the application locally using the [README](README.md) instructions. The frontend is plain HTML, CSS and JavaScript and has no build step.

Before submitting a change:

```sh
python -m unittest discover -s tests -v
python -m py_compile app.py config.py complex_cases.py prompt_catalog.py
node tests/check_frontend.mjs
node --test tests/test_frontend.mjs
```

The frontend checks use Node.js 22 or later; Node.js is not required to run LM Benchlab. For UI changes, check the Settings, Benchmark, History and Compare screens at desktop and narrow widths. Verify both light and dark themes. For prompt changes, use synthetic evidence, include review criteria for reasoning cases, and check that enabled-category filtering still works. Keep existing category IDs and saved-run formats compatible where possible.

Describe the problem, resulting behavior and checks performed in your pull request. Include a sanitized screenshot when the visible interface changes. Never attach real prompts, exports, `.env` files, API keys or databases containing private information. Report security issues through [SECURITY.md](SECURITY.md).

Runtime configuration, the application name and version belong in `config.py`. Use the `LM_BENCHLAB_*` environment namespace and `lm-benchlab` slug for new integration points. Keep `.env.example`, Compose defaults and the README consistent. Only explicitly public settings belong in the browser configuration response; keep credentials and database paths out of it. Preserve compatibility fallbacks for existing configuration and history. Local `compose.override.yaml` files, workspace tools, exports and backups are ignored by Git.
