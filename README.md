# Demo Scrape

Telegram polling bot for the Facebook + Google demo described in `req.md`.

## Quick start

1. Create a virtual environment.
2. Install the package:

```bash
pip install -e .
playwright install chrome
```

3. Copy `.env.example` to `.env` and populate the required values.
4. Run a health check:

```bash
PYTHONPATH=src python3 -m demo_scrape.cli health
```

5. Run the bot:

```bash
PYTHONPATH=src python3 -m demo_scrape.cli run-bot
```

## Notes

- `USE_STUB_RESULTS=true` lets you validate the orchestration without external services.
- Facebook collection requires a logged-in persistent Chrome profile.
- Formatter falls back to deterministic text output if Ollama is unavailable.