# PixelForge — десктоп-клиент (PyQt6)

Корень проекта — **только клиент** пакетного конвертера изображений.
Вся логика (валидация архивов, распаковка, конвертация) живёт на сервере
в [`Backend-main/`](Backend-main/README.md) и доступна **только по HTTP API**.
Локальной конвертации здесь нет: если сервер не запущен, клиент показывает
«Нет связи с API» и блокирует кнопки.

## Структура

| Файл | Роль |
|---|---|
| `main.py` | Точка входа → `ui_pyqt.run()` |
| `ui_pyqt.py` | Окно PyQt6: drag-and-drop, настройки, журнал, прогресс (только API-вызовы) |
| `api_client.py` | Python-клиент REST/SSE API (health, source, convert, abort, events…) |
| `config_manager.py` | Локальный `config.json` в `%APPDATA%/PixelForge` (настройки UI) |
| `docs/` | Документация клиента: [ARCHITECTURE.md](docs/ARCHITECTURE.md), [API.md](docs/API.md) |
| `Backend-main/` | Сервер: FastAPI + движок конвертации + веб-сайт (`desktop_ui/`) |

## Запуск

```bat
:: 1. Сервер (обязательно — конвертация идёт на нём)
cd Backend-main
python -m pip install -r requirements.txt
python server.py            # сайт: http://127.0.0.1:8000/  API: /docs

:: 2. Десктоп-клиент (отдельное окно)
cd ..
python -m pip install -r requirements.txt
python main.py
```

Адрес API задаётся переменной окружения `PIXELFORGE_API`
(по умолчанию `http://127.0.0.1:8000`). Клиент сам переподключается,
если сервер перезапустили.

Вместо десктопа можно пользоваться **веб-сайтом** на том же адресе —
он умеет то же самое (загрузка файлов/архивов, настройки, прогресс, скачивание ZIP).

## Документация

- Полный справочник HTTP API — [Backend-main/API.md](Backend-main/API.md) и Swagger `http://127.0.0.1:8000/docs`.
- Клиент: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (потоки, SSE, сигналы Qt), [docs/API.md](docs/API.md) (символы клиентских модулей).

## Соответствие ТЗ

- Выход: WebP (default) / JPEG / PNG / BMP / ICO / GIF / TIFF / AVIF, качество 10–100 (default 80), интерполяция Lanczos (default) / Bicubic / Bilinear.
- Ресайз с aspect ratio, ручной W/H.
- Пул потоков = `os.cpu_count()` (2–16), FIFO `queue.Queue`, счётчик под Mutex — на стороне сервера (`Backend-main/thread_manager.py`).
- Архивы `.zip`/`.7z`/`.rar`: потоковое чтение оглавления, валидация расширения + Magic Numbers (PNG `89 50 4E 47`, JPEG `FF D8 FF`, BMP `42 4D` и др.). Чужой файл → `ValidationError` → HTTP 400, ошибка в UI.
- Кэш `.cache_processing/` и конфиг сервера — в `%TEMP%/PixelForge` и `%APPDATA%/PixelForge` **машины сервера**; конфиг UI клиента — `%APPDATA%/PixelForge/config.json`.
- UI Dark Mode `#1E1E1E`/`#2D2D2D`, акцент `#007ACC`, ошибка `#D32F2F`, Segoe UI 10 пт. Drag-and-Drop (окно + зона) + «Обзор». Старт блокирован без валидного источника; при работе настройки заморожены, прогресс «Обработано: X из Y файлов (Z% объектов)»; по завершении — `QApplication.beep` — ТЗ п.5.3.
