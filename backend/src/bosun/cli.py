"""`bosun` command: start the server (localhost only by default)."""

from __future__ import annotations

import argparse
import os

import uvicorn

from bosun.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(prog="bosun", description="Bosun server")
    parser.add_argument("--host", default=None, help="Bind address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument(
        "--reload", action="store_true", help="Auto-reload on code changes (development)"
    )
    args = parser.parse_args()

    if args.host:
        os.environ["BOSUN_HOST"] = args.host
    if args.port:
        os.environ["BOSUN_PORT"] = str(args.port)
    os.environ.setdefault("BOSUN_LAUNCH_CWD", os.getcwd())
    get_settings.cache_clear()
    cfg = get_settings()
    uvicorn.run(
        "bosun.main:create_guarded_app",
        factory=True,
        host=cfg.host,
        port=cfg.port,
        reload=args.reload,
        log_level=cfg.log_level.lower(),
        ws_ping_interval=20,
    )


if __name__ == "__main__":
    main()
