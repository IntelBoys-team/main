# PixelForge — Справочник API (для разработчиков)

Полный перечень публичных символов всех модулей с сигнатурами, параметрами,
возвращаемыми значениями и исключениями. Общая картина — в [ARCHITECTURE.md](ARCHITECTURE.md).

Содержание:
1. [main.py](#1-mainpy)
2. [image_core.py](#2-image_corepy--ядро-конвертации)
3. [thread_manager.py](#3-thread_managerpy--пул-потоков)
4. [archive_validator.py](#4-archive_validatorpy--валидация)
5. [config_manager.py](#5-config_managerpy--хранилище)
6. [ui_pyqt.py](#6-ui_pyqtpy--ui-на-pyqt6)
7. [ui_app.py](#7-ui_apppy--legacy-ui-tkinter)

---

## 1. main.py

Точка входа. Никаких публичных функций, кроме:

```python
def main() -> int
```
Добавляет каталог проекта в `sys.path` (запуск из любого каталога), вызывает `ui_pyqt.run()`.
При `ImportError` (не установлены зависимости) печатает сообщение и подсказку
`python -m pip install -r requirements.txt`, возвращает `1`. Код возврата = код выхода Qt.

Запуск: `python main.py` (через `raise SystemExit(main())`).

---

## 2. image_core.py — ядро конвертации

Сервисный слой, чистые функции над Pillow. Никаких зависимостей от UI/потоков.

### Константы

| Константа | Значение | Смысл |
|---|---|---|
| `INTERPOLATION_MAP` | `{"Bilinear": Resampling.BILINEAR, "Bicubic": ..., "Lanczos": ...}` | Имя из UI → флаг Pillow |
| `OUTPUT_EXT` | `{"WebP": ".webp", "JPEG": ".jpg"}` | Формат → расширение файла |

### `compute_target_size(orig_w, orig_h, req_w, req_h, keep_aspect) -> tuple[int, int]`

Пересчёт целевого размера при ресайзе (aspect ratio).

| Параметр | Тип | Смысл |
|---|---|---|
| `orig_w`, `orig_h` | `int` | Текущие ширина/высота |
| `req_w`, `req_h` | `int \| None` | Запрошенные ширина/высота (`None`/`0` = не задано) |
| `keep_aspect` | `bool` | Сохранять ли пропорции |

Логика:
- обе стороны не заданы → возвращается оригинальный размер (ресайз не нужен);
- `keep_aspect=True` и обе заданы → `scale = min(req_w/orig_w, req_h/orig_h)` — **вписывание в бокс**;
- `keep_aspect=True` и одна сторона задана → масштабирование по ней, вторая считается;
- `keep_aspect=False` → заданные стороны берутся как есть, недостающие — оригинальные.

Результат всегда ≥ 1. Исключения не бросает.

### `convert_image(src_path, dst_dir, output_format="WebP", quality=80, resize_enabled=False, width=None, height=None, keep_aspect=True, interpolation="Lanczos") -> Path`

Конвертация **одного** файла. Возвращает путь к созданному файлу.

| Параметр | Тип | Смысл |
|---|---|---|
| `src_path` | `str \| Path` | Входной файл (PNG/JPEG/BMP) |
| `dst_dir` | `str \| Path` | Каталог вывода; создаётся `mkdir(parents=True)` при необходимости |
| `output_format` | `"WebP" \| "JPEG"` | Неизвестное значение молча приводится к `WebP` |
| `quality` | `int` | Приводится к диапазону `[10, 100]` |
| `resize_enabled` | `bool` | Включает ресайз |
| `width`, `height` | `int \| None` | Целевые размеры (см. `compute_target_size`) |
| `keep_aspect` | `bool` | Сохранять пропорции |
| `interpolation` | `str` | `"Lanczos" \| "Bicubic" \| "Bilinear"`; неизвестное → Lanczos |

Побочные эффекты и детали:
- **Коллизии имён:** если `stem.ext` существует — `stem_1.ext`, `stem_2.ext`, …;
- **WebP:** `method=6` (максимальное сжатие), палитра `P` → `RGBA` (прозрачность сохраняется);
- **JPEG:** режимы `RGBA/LA/PA/P` → `RGB` со сведением на **белый фон** через маску альфа;
- Вызывается из рабочих потоков `ThreadManager` — функция потокобезопасна (локальных состояний нет).

**Исключения:** любые ошибки Pillow (`UnidentifiedImageError`, `OSError` при битом файле) —
пробрасываются наружу; их ловит `ThreadManager` и передаёт в `on_error`.

---

## 3. thread_manager.py — пул потоков

### `class ThreadManager(max_workers: int | None = None)`

Пул рабочих потоков + FIFO-очередь + Mutex-счётчик (ТЗ п.2.2).

- `max_workers` по умолчанию = `clamp(os.cpu_count(), 2, 16)`.
- Внутренние поля: `_queue` (`queue.Queue[Path]`), `_lock` (`threading.Lock`),
  `_abort` (`threading.Event`), `_executor` (`ThreadPoolExecutor`), `_done`, `_total`.

#### Свойства и методы состояния

| Сигнатура | Описание |
|---|---|
| `aborted -> bool` (property) | Установлен ли флаг прерывания |
| `pending_count() -> int` | Размер очереди (`qsize()`) |
| `max_workers: int` | Размер пула (атрибут) |

#### `abort() -> None`
Экстренная остановка (кнопка «Прервать»): ставит флаг `_abort`, выгребает очередь до пустоты,
`executor.shutdown(wait=False, cancel_futures=True)`. Уже начатый файл доработается,
новые не начнутся.

#### `reset() -> None`
Снимает флаг прерывания, обнуляет `_done`/`_total`, очищает очередь. Вызывается автоматически
из `start()`, но публичен.

#### `start(files, dst_dir, convert_kwargs, on_progress=None, on_ok=None, on_error=None, on_finished=None) -> None`

**Блокирующий** запуск пакетной конвертации — вызывайте из фонового потока,
а не из GUI-потока.

| Параметр | Тип | Смысл |
|---|---|---|
| `files` | `list[Path]` | Входные файлы (кладутся в очередь целиком) |
| `dst_dir` | `Path` | Каталог вывода |
| `convert_kwargs` | `dict` | Ключи, разворачиваемые в `convert_image(src, dst, **kwargs)` |
| `on_progress` | `Callable[[done: int, total: int], None] \| None` | После **каждого** файла (в т.ч. при ошибке) |
| `on_ok` | `Callable[[src: Path, dst: Path], None] \| None` | Успешная конвертация |
| `on_error` | `Callable[[src: Path, msg: str], None] \| None` | Ошибка конкретного файла (пакет продолжается) |
| `on_finished` | `Callable[[done: int, total: int], None] \| None` | Только при **не**прерванном завершении |

Возврат: `None`. Исключения воркеров не пробрасываются (гасятся в `fu.result()`).

**Важно:** все колбэки выполняются в рабочих потоках — внутри них нельзя трогать виджеты,
только эмитить сигналы (см. архитектуру, раздел «Модель потоков»).

---

## 4. archive_validator.py — валидация

### Исключения и константы

```python
class ValidationError(Exception)   # единственный тип ошибок валидации
MAGIC_PNG   = b"\x89PNG"          # 89 50 4E 47
MAGIC_JPEG  = b"\xff\xd8\xff"     # FF D8 FF
MAGIC_BMP   = b"BM"               # 42 4D
SUPPORTED_EXTS = {".png", ".jpg", ".jpeg", ".bmp"}
ARCHIVE_EXTS   = {".zip", ".rar", ".7z"}
VALIDATION_ERROR_MSG = "Ошибка валидации: архив содержит неподдерживаемые типы данных. Конвертация невозможна"
```

### `detect_image_kind(header: bytes) -> str | None`
Определяет тип по первым байтам: возвращает `"png" | "jpeg" | "bmp"`, иначе `None`.
Принимает от 2 до 4 байт заголовка. Исключения не бросает.

### `is_archive(path: Path) -> bool`
`True`, если расширение входит в `ARCHIVE_EXTS`.

### `iter_zip_members(archive: Path) -> Iterable[str]` (генератор)
Потоковое чтение имён файлов оглавления `.zip` без распаковки (каталоги пропускаются).
Бросает `zipfile.BadZipFile` для повреждённого архива.

### `read_zip_header(archive: Path, member: str, n: int = 4) -> bytes`
Читает первые `n` байт одного файла внутри `.zip` (для проверки сигнатуры).

### `validate_and_extract(archive: Path, cache_dir: Path) -> list[Path]`

Полный цикл: **валидация оглавления → распаковка в кэш → повторная проверка сигнатур на диске**.
Возвращает **отсортированный** список извлечённых изображений (рекурсивно по `cache_dir`).

| Параметр | Тип | Смысл |
|---|---|---|
| `archive` | `Path` | `.zip` / `.7z` / `.rar` |
| `cache_dir` | `Path` | Каталог распаковки (создаётся) |

Поведение при ошибках:
- `ValidationError` — чужой/неподдерживаемый файл, пустой архив, нет изображений,
  отсутствующая зависимость для `.7z`/`.rar`; кэш **стирается** перед пробросом;
- `zipfile.BadZipFile` → конвертируется в `ValidationError` «(архив повреждён)», кэш стирается.

Валидация каждого файла: расширение + Magic Number (до 5 имён в тексте ошибки).

### `validate_single_image(path: Path) -> Path`
Валидация одиночного файла: существует, расширение поддерживается, сигнатура совпадает.
Возвращает тот же `path`. Бросает `ValidationError` (файл не найден / не поддерживается /
не читается / сигнатура не совпала).

### `collect_from_folder(folder: Path) -> list[Path]`
Рекурсивный (`rglob`) сбор файлов с поддерживаемыми расширениями, **отсортированный**.
Неподдерживаемые файлы молча пропускаются (в отличие от архивов!), исключений не бросает.

---

## 5. config_manager.py — хранилище

### Константы

```python
APP_NAME = "PixelForge"
CONFIG_FILENAME = "config.json"
CACHE_DIRNAME = ".cache_processing"
DEFAULTS = {
    "output_format": "WebP",      # WebP | JPEG
    "quality": 80,                # 10..100
    "interpolation": "Lanczos",   # Lanczos | Bicubic | Bilinear
    "output_dir": "",             # "" = авто (<родитель источника>/Converted)
    "resize_enabled": False,
    "width": 1920,
    "height": 1080,
    "keep_aspect": True,
}
```

### `get_config_path() -> Path`
`%APPDATA%/PixelForge/config.json` на Windows (с созданием каталога), иначе `./config.json`.
Путь к каталогу создаётся сразу.

### `get_cache_dir() -> Path`
`<sys temp>/PixelForge/.cache_processing/` (каталог создаётся). Кэш живёт во временном
каталоге ОС, чтобы не засорять проект.

### `class ConfigManager(path: Path | None = None)`

Хранит настройки в памяти (`self.data: dict`) и синхронизирует с диском.

| Метод | Сигнатура | Описание |
|---|---|---|
| `__init__` | `(path=None)` | `path` по умолчанию = `get_config_path()`; сразу вызывает `load()` |
| `load` | `() -> dict` | Читает JSON; при отсутствии/битом JSON — дефолты. Нормализует: `quality` → `[10,100]`, `output_format`/`interpolation` → допустимые значения. Битые/лишние ключи игнорируются. Возвращает `self.data` |
| `save` | `() -> None` | Пишет JSON (`indent=2`, `ensure_ascii=False`); ошибки OSError глотает |
| `get` | `(key, default=None)` | Значение из `self.data`, иначе `default`, иначе `DEFAULTS[key]` |
| `set` | `(key, value) -> None` | Меняет в памяти **без** записи на диск |
| `update` | `(mapping: dict) -> None` | Меняет несколько ключей и **сразу сохраняет** на диск |
| `clear_cache` | `@staticmethod (cache_dir=None) -> None` | Безвозвратно удаляет каталог кэша (`shutil.rmtree(ignore_errors=True)`) |

⚠️ `get(key, default)`: если передан `default=None`, метод вернёт значение из `DEFAULTS`.

---

## 6. ui_pyqt.py — UI на PyQt6

Presentation Layer. Окно фиксированное 800×600, тёмная тема; **только** сигналы в сервисный слой.

### Константы палитры

`BG="#1E1E1E"`, `PANEL="#2D2D2D"`, `TEXT_MAIN="#FFFFFF"`, `TEXT_SUB="#E0E0E0"`,
`ACCENT="#007ACC"` (акцент), `ERROR="#D32F2F"`, `HINT_TEXT` (текст подсказки зоны дропа),
`INTERP_ITEMS` (отображаемые названия интерполяции).

### `run() -> int`
Создаёт `QApplication`, шрифт Segoe UI 10 пт, окно `ImageConverterWindow`, возвращает `app.exec()`.

### `class DropFrame(QFrame)`
Зона Drag-and-Drop с пунктирной рамкой.

- Сигнал `file_dropped = pyqtSignal(str)` — локальный путь первого брошенного объекта.
- `dragEnterEvent` — принимает события с URL; `dropEvent` — эмитит `file_dropped`.

### `class ImageConverterWindow(QWidget)`

Главное (и единственное) окно.

#### Сигналы (единственный канал из фоновых потоков в GUI)

| Сигнал | Слот | Назначение |
|---|---|---|
| `sig_log(str)` | `_on_log` → `log_box.append` | Строка журнала |
| `sig_progress(int, int)` | `_on_progress` | `done`, `total` → прогресс-бар + счётчик |
| `sig_status(str)` | `status_label.setText` | Строкa статуса внизу |
| `sig_counter(str)` | `counter_label.setText` | Текст счётчика |
| `sig_source(str)` | `source_label.setText` | Текст в зоне дропа |
| `sig_start_enabled(bool)` | `btn_start.setEnabled` | Блокировка «Старт» |
| `sig_busy(bool)` | `_on_busy` | Режим «идёт работа»: заморозка настроек, бесконечная анимация прогресса |
| `sig_error(str)` | `_on_error` | Модальное окно «Ошибка валидации» |
| `sig_drop_error()` | `_flash_drop_error` | Красная рамка зоны дропа на 600 мс |
| `sig_beep()` | `QApplication.beep` | Системный звук по завершении |

#### Состояние (атрибуты)

| Атрибут | Тип | Смысл |
|---|---|---|
| `cfg` | `ConfigManager` | Настройки |
| `tm` | `ThreadManager` | Пул потоков (единственный на окно) |
| `source_files` | `list[Path]` | Отвалидированные входные файлы (пусто = «Старт» заблокирован) |
| `source_from_cache` | `bool` | Источник распакован в кэш → после цикла потребуется новая валидация |
| `working` | `bool` | Идёт валидация/конвертация (защита от повторного входа) |
| `cache_dir` | `Path` | Каталог кэша из `get_cache_dir()` |

#### Методы

**Построение/конфигурация UI**
- `_build()` — создаёт все виджеты и раскладку (абсолютные координаты); подробности
  комментариями в коде (зоны: дроп 12,12,776×156 → настройки 12,176,260×274 →
  журнал 282,176,506×274 → прогресс/кнопки/статус внизу).
- `_connect_signals()` — соединяет `sig_*` со слотами.
- `_panel_style() -> str` — QSS панели (`PANEL`, radius 6).
- `_sub(text) -> QLabel` — вспомогательная подпись над виджетом.

**Конфиг ↔ виджеты**
- `_interp_value() -> str` — текст комбобокса → `"Lanczos" | "Bicubic" | "Bilinear"`.
- `_apply_config_to_widgets()` — `cfg.data` → виджеты (при старте).
- `_persist()` — виджеты → `cfg.update(...)` (запись на диск), обновляет метку папки вывода.
- `_refresh_out_label()` — «Выходная папка: …» (или подсказка про `./Converted`).
- `_on_quality(v)` — обновляет «80%» и делает `_persist()`.
- `_on_resize_toggle()` — включает/выключает поля W/H по чекбоксу.

**Слоты/журнал**
- `_log(msg)` / `_on_log(msg)` — потокобезопасная запись в журнал через `sig_log`.
- `_on_progress(done, total)` — процент + текст «Обработано: X из Y файлов (Z% объектов)».
- `_on_busy(busy)` — замораживает/размораживает настройки; при `busy=True` прогресс-бар
  в режим `setRange(0, 0)` (бесконечная анимация), иначе возвращает `0..100` и состояние W/H.
- `_on_error(msg)` — `QMessageBox.critical`.
- `_flash_drop_error()` — красная рамка дроп-зоны, возврат к акценту через 600 мс.

**Drag-and-Drop**
- `dragEnterEvent` / `dropEvent` — принимают URL и вызывают `load_source_str` (DnD работает
  по всему окну и по `DropFrame`).

**Источник**
- `load_source_str(s: str)` — обёртка `load_source(Path(s))`.
- `on_browse_source()` — диалог: сначала выбор архива, при отмене — выбор папки.
- `on_browse_output()` — выбор папки вывода → `cfg.update({"output_dir": ...})`.
- `load_source(path: Path)` — точка входа валидации: если `working` — игнор; иначе
  `busy=True`, «Старт» блокируется, запускается daemon-поток `_validate_bg`.
- `_validate_bg(path: Path)` — **фоновый поток**: ветвление архив/папка/файл
  (см. ARCHITECTURE.md, шаг 1), заполнение `source_files`, эмиты успеха/ошибки.
  При `ValidationError` — лог + красная рамка + модалка; при прочем `Exception` —
  кэш стёрт, источник сброшен.

**Запуск/останов**
- `_output_dir() -> Path` — явная папка из конфига → `<родитель первого файла>/Converted` → `cwd/Converted`.
- `on_start()` — собирает `kwargs` из виджетов, логирует параметры и число потоков,
  запускает daemon-поток `_convert_bg(files, dst, kwargs)`.
- `_convert_bg(files, dst, kwargs)` — **фоновый поток**: блокирующий `tm.start(...)` с колбэками,
  в `finally` — очистка кэша, сброс источника-из-кэша, `working=False`, `busy=False`,
  разблокировка «Старт»; при успехе — `sig_beep`.
- `on_abort()` — если `working`: `tm.abort()` + запись в журнал.

---

## 7. ui_app.py — legacy UI (Tkinter)

**Не используется `main.py`** (точка входа — `ui_pyqt.py`). Альтернативная реализация UI на
`customtkinter` + опциональный `tkinterdnd2` (настоящий Drag-and-Drop).

Класс `ImageConverterApp` повторяет структуру PyQt-версии:
`_build()`, `_apply_config_to_widgets()`, `_persist()`, `load_source()`,
`_validate_bg()`, `on_start()`, `_convert_bg()`, `on_abort()`, `run()`.

Отличия от PyQt-версии: вместо Qt-сигналов — плоский вызов `self.root.after(0, ...)`
для переноса обновлений в GUI-поток; конвертация запускается через `tm.start(..., total=...)`.

Правьте эту версию только осознанно; фичи нужно дублировать в `ui_pyqt.py`.

---

## Частые сценарии — куда смотреть

| Задача | Где копать |
|---|---|
| Изменить качество/сжатие по умолчанию | `config_manager.DEFAULTS["quality"]`, `image_core.convert_image` |
| Добавить формат/архив/тип входа | Рецепты в [ARCHITECTURE.md §9](ARCHITECTURE.md) |
| Понять, почему «Старт» заблокирован | `source_files` пуст или `working=True`; эмиты `sig_start_enabled` в `_validate_bg` / `_convert_bg` |
| Почему ошибка валидации | `archive_validator`: расширение не в `SUPPORTED_EXTS` или сигнатура не совпала |
| Что делать с битым файлом | Логируется в `on_error` (`ui_pyqt._convert_bg`), пакет не прерывается |
| Где хранятся настройки пользователя | `%APPDATA%/PixelForge/config.json` |
| Где лежат распакованные архивы | `%TEMP%/PixelForge/.cache_processing/` (стирается автоматически) |
