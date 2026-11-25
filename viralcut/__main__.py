"""Запуск: python -m viralcut [--port 8765] [--host 127.0.0.1]"""
from __future__ import annotations

import argparse
import os


def main() -> None:
    parser = argparse.ArgumentParser(description="ViralCut")
    parser.add_argument("--host", default=os.environ.get("VIRALCUT_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("VIRALCUT_PORT", "8765")))
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    import uvicorn

    uvicorn.run("viralcut.main:app", host=args.host, port=args.port, reload=args.reload,
                log_level="info")


if __name__ == "__main__":
    main()
