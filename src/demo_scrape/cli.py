from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys

from demo_scrape.bot import run_polling_bot
from demo_scrape.config import AppConfig
from demo_scrape.orchestrator import DemoOrchestrator
from demo_scrape.planner import build_search_plan


def _setup_logging() -> None:
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
    )
    # Suppress noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("playwright").setLevel(logging.WARNING)


def main() -> None:
    _setup_logging()
    parser = argparse.ArgumentParser(prog="demo-scrape")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("health", help="Show configuration and dependency readiness")

    plan_parser = subparsers.add_parser("plan", help="Build the search plan for a question")
    plan_parser.add_argument("question", help="Question to parse into a search plan")

    ask_parser = subparsers.add_parser("ask", help="Run one question through the orchestrator")
    ask_parser.add_argument("question", help="Question to process")

    subparsers.add_parser("run-bot", help="Start Telegram polling bot")

    args = parser.parse_args()
    config = AppConfig.from_env()

    if args.command == "health":
        exit_code = asyncio.run(_run_health(config))
        raise SystemExit(exit_code)
    if args.command == "plan":
        plan = build_search_plan(args.question, config)
        print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))
        return
    if args.command == "ask":
        asyncio.run(_run_question(config, args.question))
        return
    if args.command == "run-bot":
        run_polling_bot(config)
        return

    raise SystemExit(2)


async def _run_health(config: AppConfig) -> int:
    ollama_status = await _probe_ollama(config)
    status = {
        "telegram_token_present": bool(config.telegram_bot_token),
        "facebook_profile_present": bool(config.facebook_profile_dir),
        "ollama_host": config.ollama_host,
        "ollama_model": config.ollama_model,
        "ollama_status": ollama_status,
        "use_stub_results": config.use_stub_results,
        "dependencies": {
            "httpx": _module_available("httpx"),
            "beautifulsoup4": _module_available("bs4"),
            "playwright": _module_available("playwright"),
            "python_telegram_bot": _module_available("telegram"),
        },
    }
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0 if status["telegram_token_present"] and ollama_status["reachable"] else 1


async def _run_question(config: AppConfig, question: str) -> None:
    orchestrator = DemoOrchestrator(config)
    plan, results, rendered = await orchestrator.handle_question(question)
    print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))
    print(json.dumps(results.to_dict(), ensure_ascii=False, indent=2))
    print(rendered)


def _module_available(name: str) -> bool:
    try:
        __import__(name)
    except ImportError:
        return False
    return True


async def _probe_ollama(config: AppConfig) -> dict:
    if not _module_available("httpx"):
        return {"reachable": False, "model_available": False, "reason": "httpx not installed"}

    import httpx

    try:
        async with httpx.AsyncClient(timeout=config.request_timeout_seconds) as client:
            response = await client.get(f"{config.ollama_host}/api/tags")
            response.raise_for_status()
    except Exception as exc:
        return {"reachable": False, "model_available": False, "reason": str(exc)}

    models = response.json().get("models", [])
    names = [item.get("name", "") for item in models]
    requested = config.ollama_model
    requested_with_latest = requested if ":" in requested else f"{requested}:latest"
    return {
        "reachable": True,
        "model_available": requested in names or requested_with_latest in names,
        "models": names,
    }


if __name__ == "__main__":
    main()