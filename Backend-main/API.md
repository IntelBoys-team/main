# PixelForge API — документация

REST/SSE API пакетной конвертации изображений (PNG/JPEG/BMP/WebP/GIF/ICO/TIFF/AVIF → 8 форматов).
Один FastAPI-сервер отдаёт и API (`/api/*`), и веб-сайт (`desktop_ui/` на `/`).
Клиенты (desktop, web) работают по HTTP.

- **Базовый URL:** `http://127.0.0.1:8000` (по умолчанию)
- **Запуск:** `python server.py [--host HOST] [--port PORT]`
- **Swagger UI:** `http://127.0.0.1:8000/docs`
- **CORS:** открыт (`*`), но **без credentials** — внешним браузерным клиентам
  нужно передавать свой идентификатор сессии (см. «Сессии»).
- **Версия API:** `2.0.0` (проверить: `GET /api/health`)

> **Состояние изолировано по сессиям.** У каждого клиента (вкладка браузера,
> desktop-процесс, API-скрипт) свой набор: источник, задача, каталоги
> загрузок/результатов/скачиваний, кэш распаковки, `config.json`, SSE-поток и
> `GET /api/download` — чужие файлы через API недоступны. Внутри одной сессии
> действует прежнее правило: новый источник сбрасывает предыдущий, конвертация
> может идти только одна, все SSE-события уходят только слушателям своей сессии.

---

## Сессии

Идентификатор сессии определяется по убыванию приоритета:

1. заголовок `X-Session-Id: <id>` — для внешних API-клиентов (в т.ч. CORS);
2. query-параметр `?session=<id>` — для `EventSource` cross-origin
   (браузерный `EventSource` не умеет задавать заголовки);
3. cookie `pf_session` — браузер и desktop-клиент: `requests.Session` шлёт её
   автоматически, **отдельных действий не требуется**;
4. если идентификатора нет — сервер генерирует его и отвечает `Set-Cookie`.

Формат id: `[A-Za-z0-9_-]{8,64}` (штатная генерация — `uuid4().hex`).
Невалидный `X-Session-Id` → `400`.

Каждая сессия живёт в `temp/PixelForge/sessions/<sid>/`:
`uploads/`, `outputs/`, `downloads/`, `cache/`, `config.json`.

Неактивные сессии (нет конвертации/валидации, нет открытого SSE, нет запросов
6 часов) удаляет фоновая уборка; осиротевшие каталогы прошлых запусков
сервера — через 24 часа. Каталоги переживают рестарт сервера (файлы остаются
на диске), но состояние задачи хранится в памяти — после рестарта задача
снова `idle`.

```bash
# stateless-скрипт: заведите свой id и передавайте его во всех запросах
SID=$(python -c "import uuid;print(uuid.uuid4().hex)")
curl -H "X-Session-Id: $SID" -F "file=@photos.zip" http://127.0.0.1:8000/api/source/upload
curl -H "X-Session-Id: $SID" -X POST http://127.0.0.1:8000/api/convert \
  -H "Content-Type: application/json" -d '{"output_format":"WebP"}'
curl -H "X-Session-Id: $SID" -OJ http://127.0.0.1:8000/api/download
```

---

## Модель данных

### Статусы задачи (`job.status`)

| Статус | Значение |
|---|---|
| `idle` | Задача ещё не создавалась |
| `validating` | Идёт проверка источника (архива/папки/файла) |
| `ready` | Источник прошёл валидацию, можно конвертировать |
| `converting` | Конвертация выполняется |
| `done` | Конвертация завершена успешно |
| `aborted` | Конвертация прервана пользователем |
| `error` | Ошибка валидации или критическая ошибка конвертации |

### Snapshot задачи (`JobSnapshot`)

```json
{
  "id": "a1b2c3d4e5f6",
  "status": "done",
  "done": 12,
  "total": 12,
  "errors": 0,
  "output_dir": "C:\\images\\Converted",
  "files": [{"name": "photo.webp", "size": 104233}],
  "can_download": true,
  "started_at": 1730000000.0,
  "finished_at": 1730000012.5,
  "logs": ["[12:00:00] Старт: формат=WebP ..."]
}
```

- `files` — только результаты, реально существующие на диске (имя + размер в байтах);
- `logs` — последние 400 строк журнала;
- `can_download == true` → доступен `GET /api/download`.

### Snapshot источника (`SourceSnapshot`)

```json
{
  "ready": true,
  "label": "photos.zip — 24 файл(ов) готово к конвертации",
  "name": "photos.zip",
  "kind": "archive",
  "count": 24,
  "from_cache": true
}
```

`kind`: `none` | `archive` | `folder` | `file` | `upload`.
`from_cache=true` → файлы лежат во временном каталоге сервера и будут удалены после цикла.

### Настройки конвертации (`SettingsIn`)

| Поле | Тип | По умолч. | Ограничения |
|---|---|---|---|
| `output_format` | `"WebP"` \| `"JPEG"` \| `"PNG"` \| `"BMP"` \| `"ICO"` \| `"GIF"` \| `"TIFF"` \| `"AVIF"` | `"WebP"` | формат должен уметь писать установленный Pillow; иначе 400 с перечнем доступных |
| `quality` | int | `80` | 10…100 |
| `interpolation` | `"Lanczos"` \| `"Bicubic"` \| `"Bilinear"` | `"Lanczos"` | |
| `resize_enabled` | bool | `false` | |
| `width` | int\|null | `1920` | 1…16384 |
| `height` | int\|null | `1080` | 1…16384 |
| `keep_aspect` | bool | `true` | |
| `output_dir` | str\|null | `null` | путь на машине сервера; `null` → `Converted/` рядом с источником или временный каталог |

`PUT /api/config` принимает ту же модель в «частичном» варианте (`ConfigIn`):
любое поле можно опустить — оно останется без изменения.

### Формат ошибок

```json
{"detail": "Источник не выбран или не прошёл валидацию"}
```

`detail` — человекочитаемое сообщение (на русском).

| Код | Когда |
|---|---|
| `400` | Ошибка валидации, неверные параметры, путь не найден, нет файлов для скачивания |
| `404` | `GET /api/download` без результатов |
| `409` | Конвертация уже выполняется / новый источник во время конвертации |

---

## Эндпоинты

### Системные

| Метод | Путь | Описание |
|---|---|---|
| GET | `/` | Веб-сайт `desktop_ui/` (HTML; статика монтирована FastAPI) |
| GET | `/api/health` | `{"status": "ok", "version": "2.0.0", "platform": "nt"}` |

```bash
curl http://127.0.0.1:8000/api/health
```

### Конфигурация

| Метод | Путь | Описание |
|---|---|---|
| GET | `/api/config` | Текущий конфиг (все ключи, включая `output_dir: ""`) |
| PUT | `/api/config` | Частичное обновление конфига; вернёт полный снимок |

```bash
curl -X PUT http://127.0.0.1:8000/api/config \
  -H "Content-Type: application/json" \
  -d '{"quality": 90, "resize_enabled": true, "width": 2560}'
```

### Источник

Источник — архив (ZIP/7Z/RAR), папка с изображениями или одиночный файл.
Валидация сквозная: расширение **и** Magic Numbers (PNG/JPEG/BMP).

| Метод | Путь | Описание |
|---|---|---|
| GET | `/api/source` | Snapshot текущего источника |
| POST | `/api/source` | Указать путь **на машине сервера** (desktop-клиент) |
| POST | `/api/source/upload` | Загрузить один архив или изображение (multipart) |
| POST | `/api/source/batch` | Загрузить пакет изображений, до 5000 файлов (Drag-and-Drop папки) |

```bash
# Путь на диске сервера
curl -X POST http://127.0.0.1:8000/api/source \
  -H "Content-Type: application/json" \
  -d '{"path": "D:/photos"}'

# Загрузка архива
curl -X POST http://127.0.0.1:8000/api/source/upload \
  -F "file=@photos.zip"

# Пакет изображений (несколько полей file)
curl -X POST http://127.0.0.1:8000/api/source/batch \
  -F "file=@a.png" -F "file=@b.jpg" -F "name=Мой пакет"
```

### Задача и конвертация

| Метод | Путь | Описание |
|---|---|---|
| GET | `/api/job` | Snapshot текущей/последней задачи |
| POST | `/api/convert` | Запустить конвертацию (асинхронно!) |
| POST | `/api/abort` | Прервать конвертацию, очистить очередь |

`POST /api/convert` **не ждёт завершения** — сразу возвращает snapshot со
статусом `converting`. Прогресс слушайте через SSE (`/api/events`) или опросом
`GET /api/job`.

```bash
curl -X POST http://127.0.0.1:8000/api/convert \
  -H "Content-Type: application/json" \
  -d '{"output_format": "WebP", "quality": 85, "resize_enabled": true, "width": 1920}'
```

### Результаты

| Метод | Путь | Описание |
|---|---|---|
| GET | `/api/output/files` | То же, что `GET /api/job` |
| POST | `/api/output/open` | Открыть папку результатов **на машине сервера** (OS file manager); вернёт `{"path": "..."}` |
| GET | `/api/download` | ZIP-архив результатов; удаляется с диска сразу после отдачи |

### События (SSE)

`GET /api/events` — поток `text/event-stream`, три типа событий:

```json
{"type": "log",   "message": "[12:00:01] OK  a.png -> a.webp"}
{"type": "state", "job": { ...JobSnapshot... }}
{"type": "source","source": { ...SourceSnapshot... }}
```

- каждые 15 с присылается комментарий `: ping` (для keep-alive/прокси);
- при подключении приходит `: connected`;
- поток не закрывается сам — клиент должен отключаться сам;
- соединение рекомендуется держать открытым с момента загрузки источника и до
  завершения задачи.

```bash
curl -N http://127.0.0.1:8000/api/events
```

---

## Типовые сценарии

### Веб-клиент (загрузка файлов)

1. `POST /api/source/upload` или `/api/source/batch` — получить `ready=true`.
2. `GET /api/config` — заполнить форму настройками по умолчанию.
3. Подписаться на `GET /api/events` (или начать опрашивать `/api/job`).
4. `POST /api/convert` — вернёт `status="converting"`.
5. Следить за `done/total` в событиях `state`.
6. По `status="done"` → `GET /api/download` (ZIP) — файлы на сервере недоступны напрямую.

### Desktop-клиент (пути на диске)

1. `POST /api/source` с `{"path": "D:/photos"}`.
2. `POST /api/convert`.
3. `POST /api/output/open` — открыть папку результатов в проводнике.

> `output_dir` при этом указывается на машине **сервера**; для веб-клиента
> рекомендуется не указывать (`null`) — результат уйдёт во временный каталог.

---

## Использование в коде

Готовый Python-клиент с функциями: **[`api_client.py`](api_client.py)**.

```python
from api_client import PixelForgeClient

client = PixelForgeClient("http://127.0.0.1:8000")

print(client.health())                    # {'status': 'ok', 'version': '2.0.0', ...}

# Полный цикл: загрузка архива → конвертация → ждём завершения
source = client.upload_file("photos.zip")
job = client.convert(output_format="WebP", quality=85)
final = client.wait_until_done(timeout=600, on_progress=lambda j: print(j["done"], "/", j["total"]))
client.download_zip("result.zip")
```

Пример для браузера (JS, без зависимостей):

> Сайт `desktop_ui/` работает **same-origin** — cookie ставится сама, ничего
> добавлять не нужно. Для cross-origin запросов заведите свой id и передавайте
> его в заголовке `X-Session-Id`, а для `EventSource` — в `?session=`.

```js
const BASE = "http://127.0.0.1:8000";
const SID = crypto.randomUUID().replaceAll("-", ""); // свой id сессии
const withSession = { "X-Session-Id": SID };

async function convertFiles(files, settings = {}) {
  const form = new FormData();
  for (const f of files) form.append("file", f);
  const src = await fetch(`${BASE}/api/source/batch`, {
    method: "POST", body: form, headers: withSession,
  }).then(r => r.json());

  const es = new EventSource(`${BASE}/api/events?session=${SID}`);
  const done = new Promise((resolve) => {
    es.addEventListener("message", (e) => {
      const ev = JSON.parse(e.data);
      if (ev.type === "state" && ["done", "aborted", "error"].includes(ev.job.status)) {
        es.close();
        resolve(ev.job);
      }
    });
  });

  await fetch(`${BASE}/api/convert`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...withSession },
    body: JSON.stringify({ output_format: "WebP", quality: 85, ...settings }),
  });

  return { source: src, job: await done };  // скачать: fetch(`${BASE}/api/download`, { headers: withSession })
}
```
