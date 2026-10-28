"""Data Layer / Storage — локальные настройки UI (config.json).

Клиентская часть: хранит только то, что нужно восстановить при следующем
запуске десктопа (папка вывода, формат, качество…). Вся конвертация и
кэш распаковки живут на сервере (Backend-main).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

APP_NAME = "PixelForge"
CONFIG_FILENAME = "config.json"

DEFAULTS: dict = {
    "output_format": "WebP",
    "quality": 80,
    "interpolation": "Lanczos",
    "output_dir": "",
    "resize_enabled": False,
    "width": 1920,
    "height": 1080,
    "keep_aspect": True,
}

#---------------------- НАЧАЛО ВСТАВКИ (ДОПУСТИМЫЕ ФОРМАТЫ + AVIF) ----------------------
ALLOWED_FORMATS = {"WebP", "JPEG", "PNG", "BMP", "ICO", "GIF", "TIFF", "AVIF"}
#---------------------- КОНЕЦ ВСТАВКИ ----------------------


def get_config_path() -> Path:
    """AppData (Windows) либо рабочая директория — по ТЗ п.1.5."""
    appdata = os.environ.get("APPDATA")
    if os.name == "nt" and appdata:
        d = Path(appdata) / APP_NAME
    else:
        d = Path.cwd()
    d.mkdir(parents=True, exist_ok=True)
    return d / CONFIG_FILENAME


class ConfigManager:
    def __init__(self, path: Path | None = None):
        self.path = path or get_config_path()
        self.data: dict = dict(DEFAULTS)
        self.load()

    def load(self) -> dict:
        try:
            if self.path.exists():
                with open(self.path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                for k, v in DEFAULTS.items():
                    if k in raw:
                        self.data[k] = raw[k]
        except (json.JSONDecodeError, OSError):
            self.data = dict(DEFAULTS)
        # Нормализация
        try:
            self.data["quality"] = max(10, min(100, int(self.data.get("quality", 80))))
        except (TypeError, ValueError):
            self.data["quality"] = 80
        if self.data.get("output_format") not in ALLOWED_FORMATS:
            self.data["output_format"] = "WebP"
        if self.data.get("interpolation") not in ("Lanczos", "Bicubic", "Bilinear"):
            self.data["interpolation"] = "Lanczos"
        return self.data

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def get(self, key: str, default=None):
        return self.data.get(key, default if default is not None else DEFAULTS.get(key))

    def set(self, key: str, value) -> None:
        self.data[key] = value

    def update(self, mapping: dict) -> None:
        self.data.update(mapping)
        self.save()