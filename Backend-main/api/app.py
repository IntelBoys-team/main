"""FastAPI application — SERVER: API + веб-клиент desktop_ui/.

Этот модуль не содержит UI-логики и никогда не запускает desktop-клиент.
Сервер отдаёт REST/SSE API на /api/* и статику сайта desktop_ui/ на "/" —
один адрес, один сервер, без отдельного статического сервера и ?api=... .

CORS открыт, чтобы API оставалось доступным для внешних веб-клиентов.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from archive_validator import ARCHIVE_EXTS, SUPPORTED_EXTS, ValidationError

from .state import AppState

VERSION = "2.0.0"

# Сайт desktop_ui/ отдаётся самим FastAPI — один адрес без редиректов
UI_DIR = Path(__file__).resolve().parent.parent / "desktop_ui"

# Значения согласованы с image_core.OUTPUT_EXT / archive_validator.OUTPUT_FORMATS
OutputFormat = Literal["WebP", "JPEG", "PNG", "BMP", "ICO", "GIF", "TIFF", "AVIF"]


class SettingsIn(BaseModel):
    output_format: OutputFormat = "WebP"
    quality: int = Field(80, ge=10, le=100)
    interpolation: Literal["Lanczos", "Bicubic", "Bilinear"] = "Lanczos"
    resize_enabled: bool = False
    width: int | None = Field(1920, ge=1, le=16384)
    height: int | None = Field(1080, ge=1, le=16384)
    keep_aspect: bool = True
    output_dir: str | None = None


class ConfigIn(BaseModel):
    output_format: OutputFormat | None = None
    quality: int | None = Field(None, ge=10, le=100)
    interpolation: Literal["Lanczos", "Bicubic", "Bilinear"] | None = None
    resize_enabled: bool | None = None
    width: int | None = Field(None, ge=1, le=16384)
    height: int | None = Field(None, ge=1, le=16384)
    keep_aspect: bool | None = None
    output_dir: str | None = None


class SourcePathIn(BaseModel):
    path: str = Field(min_length=1)


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _unique_destination(directory: Path, filename: str) -> Path:
    """Безопасное имя файла внутри каталога загрузки."""
    name = Path(filename or "image").name
    suffix = Path(name).suffix.lower()
    stem = Path(name).stem or "image"
    dest = directory / f"{stem}{suffix}"
    i = 1
    while dest.exists():
        dest = directory / f"{stem}_{i}{suffix}"
        i += 1
    return dest


def create_app() -> FastAPI:
    state = AppState()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # EventBus нужен loop, чтобы пул потоков мог присылать SSE-события.
        state.bus.bind_loop(asyncio.get_running_loop())
        try:
            yield
        finally:
            state.shutdown()

    app = FastAPI(
        title="PixelForge API",
        version=VERSION,
        description="API пакетной конвертации изображений для desktop- и веб-клиентов.",
        lifespan=lifespan,
    )
    app.state.core = state

    # Один сервер отдаёт и API (/api/*), и сайт desktop_ui/ (монтируется ниже,
    # после всех API-маршрутов). CORS — для внешних веб-клиентов.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=False,
    )

    # ------------------------------------------------------------------ system
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "version": VERSION, "platform": os.name}

    # ------------------------------------------------------------------ config
    @app.get("/api/config")
    def get_config() -> dict[str, Any]:
        return state.config_snapshot()

    @app.put("/api/config")
    def put_config(payload: ConfigIn) -> dict[str, Any]:
        values = payload.model_dump(exclude_none=True)
        return state.update_config(values)

    # ------------------------------------------------------------------ source
    @app.get("/api/source")
    def get_source() -> dict[str, Any]:
        return state.source_snapshot()

    @app.post("/api/source")
    def set_source(payload: SourcePathIn) -> dict[str, Any]:
        """Путь на машине сервера. Используется desktop-клиентом."""
        try:
            return state.prepare_source(payload.path)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=400, detail=f"Не удалось открыть путь: {exc}") from exc

    @app.post("/api/source/upload")
    async def upload_source(file: UploadFile = File(...)) -> dict[str, Any]:
        """Один архив/изображение. Готово и для desktop, и для будущего веба."""
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in (ARCHIVE_EXTS | SUPPORTED_EXTS):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Поддерживаются архивы ZIP/7Z/RAR и изображения "
                    + "/".join(sorted(e.lstrip(".") for e in SUPPORTED_EXTS))
                ),
            )
        dest = state.uploads_dir / f"{uuid.uuid4().hex}{suffix}"
        try:
            with open(dest, "wb") as out:
                while chunk := await file.read(1024 * 1024):
                    out.write(chunk)
            return await run_in_threadpool(
                state.prepare_source, dest, file.filename or dest.name, True
            )
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except OSError as exc:
            try:
                dest.unlink(missing_ok=True)
            except OSError:
                pass
            raise HTTPException(status_code=400, detail=f"Не удалось загрузить файл: {exc}") from exc
        finally:
            await file.close()

    @app.post("/api/source/batch")
    async def upload_batch(
        files: list[UploadFile] = File(...),
        name: str | None = Form(None),
    ) -> dict[str, Any]:
        """Пакет изображений (Drag-and-Drop папки).

        Клиент присылает только поддерживаемые изображения; сервер дополнительно
        проверяет расширение и Magic Numbers через prepare_source(validate_images=True).
        """
        if not files:
            raise HTTPException(status_code=400, detail="Файлы не переданы")
        if len(files) > 5000:
            raise HTTPException(status_code=400, detail="Слишком много файлов в источнике")

        batch_dir = state.uploads_dir / uuid.uuid4().hex
        batch_dir.mkdir(parents=True, exist_ok=True)
        try:
            for upload in files:
                suffix = Path(upload.filename or "").suffix.lower()
                if suffix not in SUPPORTED_EXTS:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "Источник должен содержать только поддерживаемые изображения: "
                            + "/".join(sorted(e.lstrip(".") for e in SUPPORTED_EXTS))
                        ),
                    )
                dest = _unique_destination(batch_dir, upload.filename or f"image{suffix}")
                with open(dest, "wb") as out:
                    while chunk := await upload.read(1024 * 1024):
                        out.write(chunk)

            display = name or f"{len(files)} изображений"
            return await run_in_threadpool(
                state.prepare_source, batch_dir, display, True, True
            )
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except HTTPException:
            shutil.rmtree(batch_dir, ignore_errors=True)
            raise
        except OSError as exc:
            shutil.rmtree(batch_dir, ignore_errors=True)
            raise HTTPException(status_code=400, detail=f"Не удалось загрузить файлы: {exc}") from exc
        finally:
            for upload in files:
                await upload.close()

    # ------------------------------------------------------------------ job
    @app.get("/api/job")
    def get_job() -> dict[str, Any]:
        return state.job_snapshot()

    @app.post("/api/convert")
    def convert(payload: SettingsIn) -> dict[str, Any]:
        from image_core import available_output_formats

        if payload.output_format not in available_output_formats():
            raise HTTPException(
                status_code=400,
                detail=f"Сервер не может записывать формат {payload.output_format}",
            )
        try:
            return state.start_conversion(payload)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/abort")
    def abort() -> dict[str, Any]:
        return state.abort()

    @app.get("/api/output/files")
    def output_files() -> dict[str, Any]:
        return state.job_snapshot()

    @app.post("/api/output/open")
    def open_output() -> dict[str, Any]:
        try:
            path = state.open_output()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=400, detail=f"Не удалось открыть папку: {exc}") from exc
        return {"path": path}

    @app.get("/api/download")
    async def download() -> FileResponse:
        """ZIP результатов. Понадобится веб-клиенту; desktop может не использовать."""
        try:
            zip_path = await run_in_threadpool(state.build_zip)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(
            zip_path,
            media_type="application/zip",
            filename=zip_path.name,
            background=BackgroundTask(lambda: zip_path.unlink(missing_ok=True)),
        )

    # ------------------------------------------------------------------ events
    @app.get("/api/events")
    async def events(request: Request) -> StreamingResponse:
        """SSE: log / state / source. Один контракт для desktop и веба."""

        async def stream():
            queue = state.bus.subscribe()
            try:
                yield ": connected\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15.0)
                        yield _sse(event)
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
            finally:
                state.bus.unsubscribe(queue)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ------------------------------------------------------------------ site
    # Сайт отдаётся тем же сервером на том же адресе (после всех /api/*-роутов,
    # чтобы они имели приоритет). Никакого отдельного статического сервера
    # и ?api=... — сайт работает с API по same-origin.
    if UI_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(UI_DIR), html=True), name="site")

    return app
