# PixelForge — пакетный конвертер изображений (сервер + веб-сайт)

Проект разделён на две независимые части:

- **Сервер** (`server.py`) — FastAPI: REST/SSE API на `/api/*` **и** сайт
  `desktop_ui/` на `/`. Один адрес, один сервер — без отдельного статического
  сервера, редиректов и `?api=...`.
- **Desktop-клиент** — PyQt6-приложение в корне репозитория
  (`../main.py` → `../ui_pyqt.py`). Подключается к уже запущенному серверу по
  HTTP (`PIXELFORGE_API`, по умолчанию `http://127.0.0.1:8000`) и не запускает
  сервер самостоятельно.

## Запуск

```bat
python -m pip install -r requirements.txt
python server.py
```

- Сайт: `http://127.0.0.1:8000/`
- Swagger: `http://127.0.0.1:8000/docs`
- API: `http://127.0.0.1:8000/api/health`

Сервер по сети (для внешнего веб-клиента):

```bat
python server.py --host 0.0.0.0 --port 8000
```

Другой адрес для desktop-клиента (в корне репозитория):

```bat
set PIXELFORGE_API=http://127.0.0.1:9000 && python main.py
```

## Architecture

```
server.py             Точка входа сервера: uvicorn + FastAPI.
api/app.py            FastAPI: REST, SSE, CORS + монтирование сайта desktop_ui/.
api/state.py          Оркестрация источника, задач и результатов.
api/events.py         Потокобезопасная шина SSE.
api/sessions.py       Сессии клиентов: изоляция состояния, каталогов и SSE.
desktop_ui/           Сайт (HTML/CSS/JS) по ТЗ п.5 — отдаётся FastAPI на "/".
image_core.py         Service: ресайз + WebP/JPEG/PNG/BMP/ICO/GIF/TIFF/AVIF.
archive_validator.py  Service: ZIP/7Z/RAR, Magic Numbers (PNG/JPEG/BMP/WEBP/...).
thread_manager.py     Service: ThreadPool + FIFO + Mutex-счётчик.
config_manager.py     Data: config.json + .cache_processing.
api_client.py         Пример Python-клиента (нужен `pip install requests`).
```

Desktop UI (`desktop.py`, `main.py`, `ui_pyqt.py`, `ui_app.py`) из этой папки
удалён: единственный desktop-клиент живёт в корне репозитория и работает
только через HTTP API.

## API

| Метод | Путь | Назначение |
| --- | --- | --- |
| GET | `/api/health` | Проверка сервера |
| GET/PUT | `/api/config` | Настройки конвертации |
| GET | `/api/source` | Текущий источник |
| POST | `/api/source` | Локальный путь (`{"path": "..."}`) |
| POST | `/api/source/upload` | Один архив/изображение (multipart) |
| POST | `/api/source/batch` | Пакет изображений (Drag-and-Drop папки) |
| GET | `/api/job` | Задача, прогресс, журнал |
| POST | `/api/convert` | Запуск конвертации |
| POST | `/api/abort` | Остановка и очистка очереди |
| GET | `/api/events` | SSE: `log`, `state`, `source` |
| GET | `/api/output/files` | Результаты |
| GET | `/api/download` | ZIP результатов (для веба) |
| POST | `/api/output/open` | Открыть папку результатов |
| GET | `/` | Сайт `desktop_ui/` (index.html) |

Swagger: `http://127.0.0.1:8000/docs`.

## Соответствие ТЗ

- UI: окно 800×600, Single-Page, тёмная тема `#1E1E1E` / `#2D2D2D`,
  акцент `#007ACC`, ошибки `#D32F2F`, Segoe UI 10 пт / заголовки 14 пт.
- Верхняя зона — Drag-and-Drop папки/архива, кнопка «Обзор», подсказка по ТЗ.
- Средняя зона — настройки слева, журнал справа.
- Нижняя зона — прогресс, «Обработано: X из Y файлов (Z% объектов)», «Старт»/«Прервать».
- Выход: WebP (default) / JPEG / PNG / BMP / ICO / GIF / TIFF / AVIF,
  качество 10–100, Lanczos / Bicubic / Bilinear.
- Ресайз с aspect ratio и ручным W/H.
- Пул потоков = `os.cpu_count()` (2–16), FIFO `queue.Queue`, `counter++` под `threading.Lock`.
- Архивы `.zip` / `.7z` / `.rar`: оглавление без полной распаковки, проверка расширения
  и Magic Numbers. Чужой файл → модальное окно, красная рамка, очистка кэша.
- Кэш `.cache_processing/` в системном temp очищается после цикла/аборта.
  Конфиг — `%APPDATA%/PixelForge/config.json`.
- Валидация/конвертация замораживают настройки; прогресс на валидации —
  бесконечная анимация; по завершении — негромкий сигнал (WebAudio).
