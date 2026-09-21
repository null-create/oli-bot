"""Entry point for ``python -m oli_bot.api`` and the ``oli-server`` script."""

import logging
import random

from art import text2art
from fastapi import FastAPI

from ..logger import setup_logging
from ..screens.taglines import TAGLINES
from .app import create_app, init_state

logger = logging.getLogger(__name__)


def _print_banner(
    backend: str,
    model: str,
    mode: str,
    profile: str,
    pool: str,
    host: str,
    port: int,
) -> None:
    """Print a startup banner with ASCII art logo and server config."""
    tagline = random.choice(TAGLINES)
    url = f"http://{host}:{port}"

    info_rows = [
        ("backend", backend),
        ("model", model),
        ("mode", mode),
        ("profile", profile),
        ("pool", pool),
        ("url", url),
    ]

    key_w = max(len(k) for k, _ in info_rows)

    print()
    print(text2art("oli", font="tarty1").rstrip())
    print("  The API Server\n")
    for key, val in info_rows:
        print(f"  {key:<{key_w}}  {val}")
    print(f"\n  {tagline}")
    print()


def main() -> None:
    import uvicorn

    app = create_app()
    init_state(app)

    setup_logging(log_path=app.state.config.log_file)

    backend = app.state.config.backend
    model = str(app.state.agent.backend.model or "(default)")
    mode = app.state.agent.mode
    profile = app.state.agent.profile_name
    host = app.state.config.api_host
    port = app.state.config.api_port

    pool_state = app.state.agent_pool
    if pool_state is None:
        pool_status = "off"
    elif pool_state.has_agents():
        pool_status = ", ".join(
            f"{p}: {', '.join(pool_state.list_agents(p))}"
            for p in pool_state.agent_pool
        )
    else:
        pool_status = "ENABLED BUT EMPTY — agents.yaml not found"

    _print_banner(backend, model, mode, profile, pool_status, host, port)

    logger.info(
        "starting api server host=%s port=%s backend=%s model=%s mode=%s profile=%s",
        host,
        port,
        backend,
        model,
        mode,
        profile,
    )
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()