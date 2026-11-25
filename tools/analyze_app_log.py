#!/usr/bin/env python3
"""Разбор app.log ViralCut из консоли.

Запуск:
    python tools/analyze_app_log.py            # найдёт лог сам
    python tools/analyze_app_log.py path/app.log --tail 80
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from viralcut import diagnostics  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(diagnostics.main())
