#!/usr/bin/env python3
"""Печатает версию приложения из единого источника — `viralcut/_version.py`.

Используется сборкой (build_dmg.sh), CI и релизными скриптами, чтобы номер
версии не расходился по файлам.
"""
from __future__ import annotations

import re
from pathlib import Path

VERSION_FILE = Path(__file__).resolve().parent.parent / "viralcut" / "_version.py"


def main() -> int:
    match = re.search(r'__version__\s*=\s*"([^"]+)"', VERSION_FILE.read_text(encoding="utf-8"))
    if not match:
        raise SystemExit(f"не нашёл __version__ в {VERSION_FILE}")
    print(match.group(1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
