# PixelForge — Архитектура десктоп-клиента (для разработчиков)

Корень проекта — **клиент**. Сервер (движок конвертации, валидация, кэш,
REST/SSE API, веб-сайт) живёт в [`Backend-main/`](../Backend-main/README.md);
его устройство описано в [Backend-main/README.md](../Backend-main/README.md),
HTTP-контракт — в [Backend-main/API.md](../Backend-main/API.md).

Здесь описано только то, что осталось в корне: как клиент ходит в API,
как устроены потоки и как это править.

---

## 1. Что это

Оконное приложение PyQt6 (1113×700, Dark Mode, frameless), которое:

1. показывает настройки/источник/журнал/прогресс;
2. отправляет команды на сервер (`POST /api/source`, `POST /api/convert`, `POST /api/abort`, `PUT /api/config`);
3. слушает события сервера по SSE (`GET /api/events`) и обновляет UI в реальном времени.

Никакой локальной валидации, распаковки и конвертации нет — алгоритмы только
на сервере (ТЗ п.1.4: UI ничего не считает сам).

## 2. Состав

```
┌────────────────────────────────────────────────────────────┐
│ main.py           точка входа → ui_pyqt.run()              │
│ ui_pyqt.py        окно PyQt6 (Presentation)                │
│ api_client.py     обёртка REST/SSE (PixelForgeClient)      │
│ config_manager.py локальный config.json (Data)             │
└───────────────────────────┬────────────────────────────────┘
                            │ HTTP (requests / SSE)
┌───────────────────────────▼────────────────────────────────┐
│ Backend-main: FastAPI (api/app.py) + движок конвертации    │
│ + веб-сайт desktop_ui/ на том же порту                     │
└────────────────────────────────────────────────────────────┘
```

Зависимости клиента: `PyQt6`, `requests` (см. `requirements.txt`).
У `api_client.py` и `config_manager.py` нет импортов Qt.

## 3. Точки входа

| Файл | Роль |
|---|---|
| `main.py` | `python main.py` → `ui_pyqt.run()`; при `ImportError` печатает подсказку про `pip install -r requirements.txt`, возвращает 1 |
| `ui_pyqt.py::run()` | `QApplication` + окно `ImageConverterWindow`, возвращает `app.exec()` |
| `api_client.py` | Можно запустить напрямую: `python api_client.py [base_url]` — печатает health/config/source/job |

Адрес сервера: `PIXELFORGE_API` (env) или `http://127.0.0.1:8000`.

## 4. Жизненный цикл сценария

### Шаг 0. Старт
`ImageConverterWindow.__init__`:
- `ConfigManager()` подгружает локальный `config.json` (`%APPDATA%/PixelForge/config.json`,
  на не-Windows — `./config.json`) → виджеты заполняются из настроек;
- создаётся `PixelForgeClient(API_BASE)` и **один** daemon-поток `_connection_loop`;
- «Старт» заблокирован (`connected=None` — ещё не проверяли).

### Шаг 1. Подключение: health → SSE → автопереподключение
`_connection_loop()` (один поток на всё окно):

```
while True:
    health() OK?  ── нет ──▶ sig_api_connected(False), sleep 2s, повтор
         │ да
    sig_api_connected(True) → _pull_snapshots()   # GET /api/job + GET /api/source
         │                    (журнал сервера гидратируется только 1 раз)
    for event in iter_events():  # SSE, блокирующий итератор
        _handle_event(event)     # log / state / source → Qt-сигналы
    обрыв SSE → sig_api_connected(False), sleep 1.5s, переподключение
```

При потере связи UI размораживается, статус — «Нет связи с API — запустите
python server.py»; при восстановлении снимки (`GET /api/job`, `GET /api/source`)
возвращают актуальное состояние — UI сам «починится» без действий пользователя.

### Шаг 2. Источник (Drag&Drop / «Обзор»)
`load_source(path)` → если нет связи: ошибка + статус; иначе `POST /api/source`
`{"path": "..."}`. **Путь должен быть виден машине сервера** (обычно клиент и
сервер — одна машина). Валидация/распаковка идут на сервере; ответ — snapshot
источника, события `source` приходят по SSE. Ошибка валидации → HTTP 400
`{"detail": ...}` → модальное окно + красная рамка дроп-зоны (600 мс).

### Шаг 3. Конвертация
`on_start()` → `POST /api/convert` (параметры из виджетов). Сервер запускает
пул потоков **в фоне** и сразу отвечает `status="converting"`. Прогресс идёт
событиями `state` (SSE) → `sig_progress(done, total)` → полоса + счётчик
«Обработано: X из Y файлов (Z% объектов)»; логи строками `log`.
По `done` — `QApplication.beep`.

`on_abort()` → `POST /api/abort` (сервер останавливает пул и чистит очередь/кэш).

### Шаг 4. Настройки
Любое изменение виджета → `_persist()`: локально `ConfigManager.update()`
(восстановление при следующем запуске) + фоновый `PUT /api/config`
(сервер хранит канонический конфиг; на всякий случай те же значения
передаются и в `POST /api/convert`).

## 5. Модель потоков (самое важное при правках)

```
GUI-поток (Qt)
│  load_source()/on_start()/on_abort()
│        │ threading.Thread(daemon=True)
│        ▼
│  _set_source_bg / _start_bg / _abort_bg / _persist_bg   — синхронный HTTP
│        │ pyqtSignal.emit()
│        ▼
│  слоты в GUI-потоке
│
└─ _connection_loop (единственный daemon-поток):
      health → _pull_snapshots → iter_events() (блокирующий SSE)
      события → sig_log / sig_job / sig_source_event / sig_api_connected
```

**Правило:** из фоновых потоков **только** эмитить `pyqtSignal`; любые
виджеты трогаются исключительно в слотах GUI-потока.

Особый случай — `sig_refresh → _refresh_controls()`: слот пересчитывает
busy/кнопку «Старт» **из актуальных флагов** (`working`, `_local_busy`,
`connected`, `source_ready`), а не из переданных bool. Поэтому из любого
потока безопасно эмитить `sig_refresh` — и получить финальное состояние,
не зависящее от порядка доставки сигналов.

Флаги состояния:

| Флаг | Смысл |
|---|---|
| `connected: bool \| None` | `None` = ещё не проверяли (статус ожидания, а не «нет связи») |
| `source_ready` | последний snapshot источника `ready=true` |
| `working` | задача на сервере в `validating`/`converting` |
| `_local_busy` | наш HTTP-запрос в полёте (источник/старт) |

«Старт» включён ⇔ `connected and source_ready and not (working or _local_busy)`.

## 6. Хранилище

| Что | Где | Кем |
|---|---|---|
| Настройки UI (папка вывода, формат, качество, ресайз…) | `%APPDATA%/PixelForge/config.json` (не-Windows — `./config.json`) | `ConfigManager` клиента |
| Канонический конфиг конвертации, кэш распаковки, результаты | `%APPDATA%/PixelForge`, `%TEMP%/PixelForge/.cache_processing/` **машины сервера** | `Backend-main/config_manager.py` |

`ConfigManager.get(key, default)`: при `default=None` вернёт значение из
`DEFAULTS` (не `None`) — хитрое поведение, при сомнениях используйте `cfg.data`.

## 7. Валидация — целиком на сервере

Клиент валидацию не выполняет и не дублирует. Проверка расширения + Magic
Number каждого файла внутри архива, распаковка в кэш и повторная проверка
сигнатур на диске — `Backend-main/archive_validator.py`. Ошибка приходит как
HTTP 400 с текстом в `detail` (список проблемных имён — до 5 штук).

Что это меняет для пользователя клиента: файлы, брошенные в окно, — это
**путь**, который отправляется серверу; содержимое по сети не гоняется.

## 8. Как это расширять — краткие рецепты

**Новое поле настроек:**
1. `config_manager.DEFAULTS` (и нормализация в `load()`, если нужна);
2. виджет в `ui_pyqt._build()` + `_apply_config_to_widgets()` + `_persist()`;
3. ключ в `payload` в `on_start()` — и **обязательно** в модели `SettingsIn`
   сервера ([Backend-main/API.md](../Backend-main/API.md)).

**Новое действие над сервером:**
1. метод в `PixelForgeClient` (`api_client._request("POST", "/api/...")`);
2. daemon-поток-обёртка в `ui_pyqt` (`_xxx_bg`) с эмитом Qt-сигнала;
3. обработка в слоте GUI-потока.

**Новый формат вывода / тип архива / входное изображение:**
правится только сервер: `Backend-main/image_core.py`, `archive_validator.py`,
`config_manager.ALLOWED_FORMATS`, модель `OutputFormat` в `api/app.py` и
`desktop_ui/index.html` (список форматов). В `ui_pyqt.py` — только пункт
`OUTPUT_FORMATS_UI`, если нужно показать его в десктопе.

## 9. Конвенции проекта

- Python 3.11+, `from __future__ import annotations` в каждом модуле.
- `pathlib.Path` на границах модулей.
- Аннотации типов у публичных функций; русскоязычные docstring'и.
- Русские строки в UI/журнале; статусы — константы из `api_client`.
- Имя переменной `job` = snapshot задачи, `source` = snapshot источника
  (те же структуры, что в [Backend-main/API.md](../Backend-main/API.md)).

## 10. Известные особенности

- Клиент **однопоточный по API**: SSE-соединение одно, очередь событий —
  FIFO. Долгий HTTP-запрос (upload пачки до 5000 файлов) не блокирует SSE.
- `output_dir` — путь **на машине сервера**. У далёкого клиента он бессмысленен:
  сервер положит результат во временный каталог, если путь недоступен.
- Перезапуск сервера: клиент переподключается сам (≤2 с), но запущенная на
  сервере задача при рестарте теряется — UI вернётся в `ready`.
- Кнопка «Папка вывода» открывает `QFileDialog` локально; путь передаётся
  серверу как есть.
- Окно frameless: перемещение — только за шапку (`title_bar`).
