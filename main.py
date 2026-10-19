"""Точка входа. Presentation -> Service -> Data."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> int:
    try:
        from ui_pyqt import run
        return run()
    except ImportError as e:
        print(e)
        print("Выполните: python -m pip install -r requirements.txt")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
