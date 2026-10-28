"""PixelForge API client — обёртка REST/SSE API над удобными функциями.

Зависимости (только у клиента, не у сервера):
    pip install requests

Пример (полный цикл):
    from api_client import PixelForgeClient

    client = PixelForgeClient("http://127.0.0.1:8000")
    client.health()
    client.upload_file("photos.zip")          # или client.set_source_path("D:/photos")
    client.convert(output_format="WebP", quality=85)
    job = client.wait_until_done(on_progress=lambda j: print(j["done"], "/", j["total"]))
    client.download_zip("result.zip")

Веб-клиент: см. раздел «Использование в коде» в API.md (EventSource + fetch).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Iterator

import requests

DEFAULT_BASE_URL = "http://127.0.0.1:8000"

# Статусы задачи из api/state.py
STATUS_IDLE = "idle"
STATUS_VALIDATING = "validating"
STATUS_READY = "ready"
STATUS_CONVERTING = "converting"
STATUS_DONE = "done"
STATUS_ABORTED = "aborted"
STATUS_ERROR = "error"

TERMINAL_STATUSES = {STATUS_DONE, STATUS_ABORTED, STATUS_ERROR}


class APIError(Exception):
    """Ошибка ответа сервера (4xx/5xx). detail — сообщение из {"detail": ...}."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(f"HTTP {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class PixelForgeClient:
    """Тонкая обёртка над PixelForge API (см. API.md).

    Все методы синхронные и бросают APIError при ошибке сервера.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()

    # ------------------------------------------------------------------ helpers
    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        kwargs.setdefault("timeout", self.timeout)
        resp = self.session.request(method, f"{self.base_url}{path}", **kwargs)
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except ValueError:
                detail = resp.text
            raise APIError(resp.status_code, str(detail))
        if resp.headers.get("content-type", "").startswith("application/json"):
            return resp.json()
        return resp

    # ------------------------------------------------------------------ system
    def health(self) -> dict[str, Any]:
        """GET /api/health — проверка доступности сервера и его версии."""
        return self._request("GET", "/api/health")

    def info(self) -> dict[str, Any]:
        """GET / — на веб-сервере это сайт desktop_ui/ (HTML), не JSON.

        Для проверки сервера используйте health().
        """
        return self._request("GET", "/")

    # ------------------------------------------------------------------ config
    def get_config(self) -> dict[str, Any]:
        """GET /api/config — текущая конфигурация (полный словарь)."""
        return self._request("GET", "/api/config")

    def update_config(self, **fields: Any) -> dict[str, Any]:
        """PUT /api/config — частичное обновление конфигурации.

        Допустимые поля: output_format, quality, interpolation, resize_enabled,
        width, height, keep_aspect, output_dir. Лишние/None игнорируются.
        Возвращает новый полный конфиг.
        """
        return self._request("PUT", "/api/config", json=fields)

    # ------------------------------------------------------------------ source
    def get_source(self) -> dict[str, Any]:
        """GET /api/source — snapshot текущего источника (ready/count/kind...)."""
        return self._request("GET", "/api/source")

    def set_source_path(self, path: str | Path) -> dict[str, Any]:
        """POST /api/source — указать путь к архиву/папке/файлу на машине сервера.

        Используйте, когда клиент и сервер — одна машина (desktop).
        Бросает APIError(400) при ошибке валидации, APIError(409) если идёт конвертация.
        """
        return self._request("POST", "/api/source", json={"path": str(path)})

    def upload_file(self, file_path: str | Path) -> dict[str, Any]:
        """POST /api/source/upload — загрузить один архив (ZIP/7Z/RAR) или изображение."""
        path = Path(file_path)
        with open(path, "rb") as fh:
            return self._request(
                "POST",
                "/api/source/upload",
                files={"file": (path.name, fh)},
            )

    def upload_batch(
        self,
        file_paths: list[str | Path],
        name: str | None = None,
    ) -> dict[str, Any]:
        """POST /api/source/batch — загрузить пакет изображений PNG/JPEG/BMP (до 5000).

        Аналог Drag-and-Drop папки в веб-клиенте. name — отображаемое имя пакета.
        """
        files = []
        handles = []
        try:
            for fp in file_paths:
                p = Path(fp)
                fh = open(p, "rb")
                handles.append(fh)
                files.append(("file", (p.name, fh)))
            data = {"name": name} if name else {}
            return self._request("POST", "/api/source/batch", files=files, data=data)
        finally:
            for fh in handles:
                fh.close()

    # ------------------------------------------------------------------ job
    def get_job(self) -> dict[str, Any]:
        """GET /api/job — snapshot задачи (status/done/total/files/logs...)."""
        return self._request("GET", "/api/job")

    def convert(
        self,
        output_format: str = "WebP",
        quality: int = 80,
        interpolation: str = "Lanczos",
        resize_enabled: bool = False,
        width: int | None = 1920,
        height: int | None = 1080,
        keep_aspect: bool = True,
        output_dir: str | None = None,
    ) -> dict[str, Any]:
        """POST /api/convert — запустить конвертацию в фоне.

        Не блокирует выполнение: сразу возвращает snapshot со статусом "converting".
        output_dir — путь на машине сервера; None → рядом с источником
        (или временный каталог, если источник загружен по HTTP).
        Бросает APIError(409), если задача уже выполняется,
        APIError(400), если источник не выбран/не валиден.
        """
        payload = {
            "output_format": output_format,
            "quality": quality,
            "interpolation": interpolation,
            "resize_enabled": resize_enabled,
            "width": width,
            "height": height,
            "keep_aspect": keep_aspect,
            "output_dir": output_dir,
        }
        return self._request("POST", "/api/convert", json=payload)

    def abort(self) -> dict[str, Any]:
        """POST /api/abort — прервать конвертацию (остановка пула, очистка очереди)."""
        return self._request("POST", "/api/abort")

    def wait_until_done(
        self,
        timeout: float = 600.0,
        poll_interval: float = 0.5,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Опрос GET /api/job до терминального статуса (done/aborted/error).

        on_progress вызывается при каждом изменении done/total/status.
        Бросает TimeoutError, если задача не завершилась за timeout секунд.
        Для событий в реальном времени используйте iter_events().
        """
        deadline = time.monotonic() + timeout
        last_key: tuple | None = None
        while True:
            job = self.get_job()
            key = (job["status"], job["done"], job["total"])
            if key != last_key:
                last_key = key
                if on_progress:
                    on_progress(job)
            if job["status"] in TERMINAL_STATUSES:
                return job
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Задача не завершилась за {timeout}s (status={job['status']})"
                )
            time.sleep(poll_interval)

    # ------------------------------------------------------------------ output
    def output_files(self) -> dict[str, Any]:
        """GET /api/output/files — синоним get_job() (файлы результата в files)."""
        return self._request("GET", "/api/output/files")

    def open_output(self) -> str:
        """POST /api/output/open — открыть папку результатов в проводнике сервера.

        Возвращает путь к папке. Работает только если клиент и сервер — одна машина.
        """
        return self._request("POST", "/api/output/open")["path"]

    def download_zip(self, dest: str | Path) -> Path:
        """GET /api/download — скачать ZIP результатов.

        Бросает APIError(404), если результатов ещё нет.
        """
        dest = Path(dest)
        with self.session.get(
            f"{self.base_url}/api/download", timeout=self.timeout, stream=True
        ) as resp:
            if resp.status_code >= 400:
                try:
                    detail = resp.json().get("detail", resp.text)
                except ValueError:
                    detail = resp.text
                raise APIError(resp.status_code, str(detail))
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=1024 * 256):
                    fh.write(chunk)
        return dest

    # ------------------------------------------------------------------ events
    def iter_events(self, stop: Callable[[dict[str, Any]], bool] | None = None) -> Iterator[dict[str, Any]]:
        """GET /api/events — итератор по SSE-потоку (блокирующий!).

        Yields словари-события:
            {"type": "log", "message": "..."}
            {"type": "state", "job": {...}}
            {"type": "source", "source": {...}}
        Комментарии (: ping / : connected) пропускаются.
        stop(event) → True закрывает поток изнутри (вызывающий делает break/return).
        Соединение закрывается при выходе из итератора (with-семантика через gc).
        """
        with self.session.get(
            f"{self.base_url}/api/events",
            stream=True,
            timeout=(self.timeout, None),  # read timeout бесконечный: сервер шлёт ping
            headers={"Accept": "text/event-stream"},
        ) as resp:
            resp.raise_for_status()
            for raw in resp.iter_lines(decode_unicode=True):
                if not raw or raw.startswith(":"):
                    continue  # ping/connected и пустые строки
                if not raw.startswith("data:"):
                    continue
                event = json.loads(raw[len("data:"):].strip())
                yield event
                if stop is not None and stop(event):
                    return


# ------------------------------------------------------------------ функции-обёртки
# Ниже — готовые функции для типовых сценариев, если не нужен клиентский класс.

def check_server(base_url: str = DEFAULT_BASE_URL) -> bool:
    """True, если сервер доступен и жив."""
    try:
        return PixelForgeClient(base_url).health().get("status") == "ok"
    except (requests.RequestException, APIError):
        return False


def convert_archive(
    archive: str | Path,
    dest_zip: str | Path | None = None,
    base_url: str = DEFAULT_BASE_URL,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    **settings: Any,
) -> dict[str, Any]:
    """Полный цикл: загрузить архив → сконвертировать → вернуть финальный job.

    dest_zip — если задан, результат дополнительно скачивается в этот ZIP.
    settings передаются в convert() (output_format, quality и т.д.).
    Бросает TimeoutError (10 мин) или APIError.
    """
    client = PixelForgeClient(base_url)
    client.upload_file(archive)
    client.convert(**settings)
    job = client.wait_until_done(timeout=600.0, on_progress=on_progress)
    if dest_zip and job["can_download"]:
        client.download_zip(dest_zip)
    return job


def convert_local_path(
    path: str | Path,
    base_url: str = DEFAULT_BASE_URL,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    output_dir: str | None = None,
    **settings: Any,
) -> dict[str, Any]:
    """Полный цикл для desktop: путь на диске → конвертация → финальный job.

    output_dir — папка результатов на машине сервера (None → "Converted" рядом с источником).
    """
    client = PixelForgeClient(base_url)
    client.set_source_path(path)
    job = client.convert(output_dir=output_dir, **settings)
    return client.wait_until_done(timeout=600.0, on_progress=on_progress)


if __name__ == "__main__":
    # Быстрая проверка: python api_client.py [base_url]
    import sys

    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BASE_URL
    c = PixelForgeClient(url)
    try:
        print("health:", c.health())
        print("config:", c.get_config())
        print("source:", c.get_source())
        print("job:   ", {k: c.get_job()[k] for k in ("status", "done", "total", "errors")})
    except APIError as exc:
        print(f"Сервер недоступен или ответил ошибкой: {exc}")
        raise SystemExit(1)
