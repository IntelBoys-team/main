# PixelForge — Справочник API десктоп-клиента

Корень проекта — **клиент**: здесь описаны символы клиентских модулей.
HTTP-контракт сервера (эндпоинты, модели, коды ошибок) — в
[Backend-main/API.md](../Backend-main/API.md).

Содержание:
1. [main.py](#1-mainpy)
2. [config_manager.py](#2-config_managerpy--локальные-настройки-ui)
3. [api_client.py](#3-api_clientpy--клиент-restsse)
4. [ui_pyqt.py](#4-ui_pyqtpy--окно-pyqt6)

---

## 1. main.py

```python
def main() -> int
```
Добавляет каталог проекта в `sys.path`, вызывает `ui_pyqt.run()`.
При `ImportError` печатает сообщение и подсказку
`python -m pip install -r requirements.txt`, возвращает `1`.

Запуск: `python main.py`.

---

## 2. config_manager.py — локальные настройки UI

Хранит только настройки окна (формат, качество, папка вывода…).
Конвертация/кэш — на сервере, здесь их нет.

### Константы

```python
APP_NAME = "PixelForge"
CONFIG_FILENAME = "config.json"
ALLOWED_FORMATS = {"WebP", "JPEG", "PNG", "BMP", "ICO", "GIF", "TIFF", "AVIF"}
DEFAULTS = {
    "output_format": "WebP",
    "quality": 80,                # 10..100
    "interpolation": "Lanczos",   # Lanczos | Bicubic | Bilinear
    "output_dir": "",             # "" = авто (рядом с источником / Converted)
    "resize_enabled": False,
    "width": 1920,
    "height": 1080,
    "keep_aspect": True,
}
```

### `get_config_path() -> Path`
`%APPDATA%/PixelForge/config.json` на Windows (каталог создаётся), иначе `./config.json`.

### `class ConfigManager(path: Path | None = None)`

| Метод | Описание |
|---|---|
| `__init__(path=None)` | `path` по умолчанию = `get_config_path()`; сразу `load()` |
| `load() -> dict` | Читает JSON; при отсутствии/битом JSON — дефолты. Нормализует: `quality` → `[10,100]`, `output_format`/`interpolation` → допустимые значения. Возвращает `self.data` |
| `save() -> None` | Пишет JSON (`indent=2`, `ensure_ascii=False`); ошибки OSError глотает |
| `get(key, default=None)` | Значение из `self.data`, иначе `default`, иначе `DEFAULTS[key]` |
| `set(key, value) -> None` | Меняет в памяти **без** записи на диск |
| `update(mapping) -> None` | Меняет несколько ключей и **сразу сохраняет** на диск |

⚠️ `get(key, default)`: при `default=None` вернёт значение из `DEFAULTS`, а не `None`.

---

## 3. api_client.py — клиент REST/SSE

Обёртка над HTTP API сервера (см. [Backend-main/API.md](../Backend-main/API.md)).
Требует `pip install requests`. Все методы синхронные; ошибки сервера — `APIError`.

### `class APIError(Exception)`
`status_code: int`, `detail: str` (текст из `{"detail": ...}`).

### `class PixelForgeClient(base_url="http://127.0.0.1:8000", timeout=30.0, session=None)`

| Группа | Метод | Эндпоинт / поведение |
|---|---|---|
| system | `health()`, `info()` | `GET /api/health`, `GET /` |
| config | `get_config()`, `update_config(**fields)` | `GET`/`PUT /api/config` (частичное обновление) |
| source | `get_source()`, `set_source_path(path)`, `upload_file(path)`, `upload_batch(paths, name=None)` | `GET /api/source`, `POST /api/source` (путь на машине сервера), `POST /api/source/upload`, `POST /api/source/batch` |
| job | `get_job()`, `convert(...)`, `abort()` | `GET /api/job`, `POST /api/convert` (асинхронно!), `POST /api/abort` |
| helper | `wait_until_done(timeout=600, poll_interval=0.5, on_progress=None)` | Опрос до `done/aborted/error`; `TimeoutError` по таймауту |
| output | `output_files()`, `open_output()`, `download_zip(dest)` | `GET /api/output/files`, `POST /api/output/open` (проводник сервера), `GET /api/download` → ZIP |
| events | `iter_events(stop=None)` | Блокирующий итератор по SSE: `{"type": "log"|"state"|"source", ...}`; `: ping`/`: connected` пропускаются; `stop(event) → True` закрывает поток |

`convert(output_format="WebP", quality=80, interpolation="Lanczos",
resize_enabled=False, width=1920, height=1080, keep_aspect=True, output_dir=None)`
— `output_dir` путь **на машине сервера**, `None` → `Converted/` рядом с источником.

### Функции-обёртки

```python
check_server(base_url) -> bool
convert_archive(archive, dest_zip=None, base_url=..., on_progress=None, **settings) -> dict
convert_local_path(path, base_url=..., on_progress=None, output_dir=None, **settings) -> dict
```

`python api_client.py [base_url]` — быстрая проверка: печатает health/config/source/job.

---

## 4. ui_pyqt.py — окно на PyQt6

Presentation Layer: окно 1113×700, frameless, Dark Mode; **только** HTTP-вызовы
и Qt-сигналы, никаких вычислений.

### Константы

`BG="#1E1E1E"`, `PANEL="#2D2D2D"`, `TEXT_MAIN`, `TEXT_SUB`, `ACCENT="#007ACC"`,
`ERROR="#D32F2F"`, `HINT_TEXT`, `INTERP_ITEMS`, `OUTPUT_FORMATS_UI` (8 форматов),
`API_BASE` (`PIXELFORGE_API` env или `http://127.0.0.1:8000`), `OFFLINE_STATUS`.

### `run() -> int`
`QApplication` + шрифт Segoe UI 10 пт + окно `ImageConverterWindow` → `app.exec()`.

### `class DropFrame(QFrame)`
Зона Drag-and-Drop: сигнал `file_dropped(str)`; hover-подсветка, анимация иконки;
клик → `on_browse_source()` родительского окна.

### `class ImageConverterWindow(QWidget)`

**Сигналы (единственный канал из фоновых потоков в GUI):**

| Сигнал | Слот | Назначение |
|---|---|---|
| `sig_log(str)` | `_on_log` → `log_box.append` | Строка журнала |
| `sig_progress(int, int)` | `_on_progress` | `done`, `total` → прогресс + счётчик |
| `sig_status(str)` | `status_label.setText` | Статус внизу |
| `sig_counter(str)` / `sig_source(str)` | `setText` | Счётчик / текст дроп-зоны |
| `sig_busy(bool)` | `_on_busy` | Заморозка настроек, бесконечная анимация прогресса |
| `sig_error(str)` | `_on_error` | Модальное окно |
| `sig_drop_error()` | `_flash_drop_error` | Красная рамка дроп-зоны 600 мс |
| `sig_beep()` | `QApplication.beep` | Звук по завершении |
| `sig_api_connected(bool)` | `_on_api_connected` | Онлайн/офлайн (health + обрыв SSE) |
| `sig_job(dict)` | `_on_job` | Snapshot задачи: статус → busy/кнопки/прогресс/статусная строка |
| `sig_source_event(dict)` | `_on_source_snapshot` | Snapshot источника: `source_ready`, метка |
| `sig_refresh()` | `_refresh_controls` | Пересчёт busy/«Старт» **из актуальных флагов** |

**Состояние:** `cfg` (ConfigManager), `client` (PixelForgeClient),
`connected: bool | None`, `source_ready`, `working`, `_local_busy`,
`_busy_applied`, `_last_job_status`, `_server_logs_hydrated`.

**Потоки:**
- `_connection_loop()` — единственный daemon-поток: `health()` →
  `_pull_snapshots()` (`GET /api/job` + `/api/source`, журнал гидратируется
  один раз) → `for event in client.iter_events(): _handle_event(event)`;
  обрыв → переподключение (1.5–2 с), `RuntimeError` → выход (окно уничтожено).
- `_set_source_bg(path)`, `_start_bg(payload)`, `_abort_bg()`, `_persist_bg(values)` —
  короткие daemon-потоки на один HTTP-запрос; в `finally` — `_local_busy=False` + `_request_refresh()`.

**Источник:** `load_source_str(s)` / `load_source(path)` → офлайн: ошибка; иначе
`_local_busy=True`, `POST /api/source`. `APIError(409)` — конвертация уже идёт;
`APIError(400)` — валидация (лог + красная рамка + модалка).

**Конвертация:** `on_start()` — `_persist()`, собирает payload из виджетов,
`POST /api/convert` в фоне. `on_abort()` — `POST /api/abort` (только если `working`).

**`_refresh_controls()`** — единственное место, где считаются busy и enabled
кнопки «Старт» (`connected and source_ready and not busy`); вызывается по
`sig_refresh` из любого потока (см. [ARCHITECTURE.md §5](ARCHITECTURE.md)).

---

## Частые сценарии — куда смотреть

| Задача | Где копать |
|---|---|
| Другой адрес сервера | env `PIXELFORGE_API` (константа `API_BASE`) |
| Новое поле настроек | `config_manager.DEFAULTS` → виджет → `_persist()` → payload `on_start()` → модель `SettingsIn` на сервере |
| Новое действие над сервером | метод в `PixelForgeClient` → `_xxx_bg` в `ui_pyqt` → слот GUI-потока |
| Новый формат вывода / архив | только сервер: `Backend-main/image_core.py`, `archive_validator.py`, `api/app.py`, `desktop_ui/index.html`; в десктопе — `OUTPUT_FORMATS_UI` |
| Почему «Старт» заблокирован | нет связи / нет источника / идёт работа — `_refresh_controls()` |
| Ошибка валидации | придёт HTTP 400 с `detail` (валидация целиком на сервере) |
| Настройки пользователя | `%APPDATA%/PixelForge/config.json` (клиент); канонический конфиг — на сервере |
