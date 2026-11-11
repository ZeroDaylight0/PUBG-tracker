# Contributing

Thanks for your interest in improving PUBG Tracker!

## Development setup

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt   # Windows: .venv\Scripts\pip
python app.py
```

## Running tests

```bash
python -m pytest tests
node --test tests/
```

Please make sure both Python and JavaScript tests pass before opening a pull request.

If you change Tailwind classes, rebuild the stylesheet with `npm ci && npm run build:css`
and commit `tailwind.css`.

## Guidelines

- Keep pull requests focused on one change.
- Add or update tests for parser and telemetry changes (`tests/fixtures/` holds sample data).
- Do not commit API keys, personal match data or large telemetry dumps.
- Use [Conventional Commits](https://www.conventionalcommits.org/) style messages
  (`feat:`, `fix:`, `docs:`, `chore:`).
