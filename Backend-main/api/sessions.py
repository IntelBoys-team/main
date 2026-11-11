"""Per-session изоляция состояния сервера.

Каждый клиент (веб-вкладка, desktop-процесс, API-скрипт) получает свой
идентификатор сессии и свой набор временных каталогов:

    temp/PixelForge/sessions/<sid>/{uploads,outputs,downloads,cache,config.json}

Идентификатор определяется так (по убыванию приоритета):

    1. заголовок ``X-Session-Id`` — внешние API-клиенты (CORS);
    2. query-параметр ``?session=`` — для EventSource cross-origin (браузерный
       EventSource не умеет задавать заголовки);
    3. cookie ``pf_session`` — браузер и desktop (``requests.Session``
       отправляет cookie автоматически, контракт API не меняется);
    4. сгенерированный — ответ получает ``Set-Cookie``.

Состояние (AppState), каталоги, EventBus/SSE и ``/api/download`` у каждой
сессии свои: чужие файлы через API недоступны.
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import threading
import time
import uuid
from http.cookies import SimpleCookie
from typing import Any, Callable, MutableMapping
from urllib.parse import parse_qs

from starlette.datastructures import Headers

from .state import AppState, get_sessions_root

COOKIE_NAME = "pf_session"
SESSION_HEADER = "X-Session-Id"

# Допустимый формат id: только [A-Za-z0-9_-] (без "." и "/" — id попадает в
# имя каталога), длина 8..64. uuid4().hex (32 символа) — штатная генерация.
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

# Уборка: неактивная сессия без открытых SSE и конвертаций удаляется через
# IDLE_TTL; осиротевшие каталоги прошлых запусков сервера — через ORPHAN_TTL.
IDLE_TTL = 6 * 3600
ORPHAN_TTL = 24 * 3600
SWEEP_INTERVAL = 300


def _cookie_session_id(header: str | None) -> str | None:
    """Достаёт валидный sid из заголовка Cookie; невалидный — None."""
    if not header:
        return None
    try:
        jar = SimpleCookie()
        jar.load(header)
        morsel = jar.get(COOKIE_NAME)
    except Exception:  # noqa: BLE001 — битый Cookie-заголовок не должен ронять запрос
        return None
    if morsel is None:
        return None
    value = morsel.value
    return value if SESSION_ID_RE.match(value) else None


def _query_session_id(query: bytes) -> str | None:
    """Достаёт валидный sid из ?session=...; невалидный — None (молча игнор)."""
    if not query:
        return None
    try:
        values = parse_qs(query.decode("latin-1", errors="ignore")).get("session")
    except Exception:  # noqa: BLE001
        return None
    if not values:
        return None
    value = values[0]
    return value if SESSION_ID_RE.match(value) else None


class SessionRegistry:
    """Словарь session_id → AppState с ленивым созданием и уборкой."""

    def __init__(self) -> None:
        self._states: dict[str, AppState] = {}
        self._last_seen: dict[str, float] = {}
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """EventBus каждой сессии доставляет SSE через главный loop сервера."""
        self._loop = loop

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex

    def get(self, session_id: str) -> AppState:
        """Возвращает (создавая при необходимости) состояние сессии."""
        with self._lock:
            state = self._states.get(session_id)
            if state is None:
                state = AppState(session_id=session_id)
                if self._loop is not None:
                    state.bus.bind_loop(self._loop)
                self._states[session_id] = state
            self._last_seen[session_id] = time.time()
            return state

    def for_request(self, request: Any) -> AppState:
        """Состояние для запроса: sid из middleware (scope["state"])."""
        sid = None
        state_scope = request.scope.get("state")
        if isinstance(state_scope, dict):
            candidate = state_scope.get("session_id")
            if isinstance(candidate, str) and SESSION_ID_RE.match(candidate):
                sid = candidate
        if sid is None:  # middleware не сработал — не должны упасть
            sid = self.new_id()
        return self.get(sid)

    def sweep(self) -> None:
        """Удаляет неактивные сессии и осиротевшие каталоги на диске."""
        now = time.time()
        root = get_sessions_root()

        with self._lock:
            victims: list[tuple[str, AppState]] = []
            for sid, seen in list(self._last_seen.items()):
                state = self._states.get(sid)
                if state is None:
                    self._last_seen.pop(sid, None)
                    continue
                if now - seen < IDLE_TTL:
                    continue
                # Конвертация/валидация или открытый SSE — сессия жива.
                if state.busy or state.bus.subscriber_count:
                    continue
                self._states.pop(sid, None)
                self._last_seen.pop(sid, None)
                victims.append((sid, state))
            known = set(self._states)

        for sid, state in victims:
            try:
                state.shutdown()
            except Exception:  # noqa: BLE001 — уборка не должна ронять сервер
                pass
            shutil.rmtree(root / sid, ignore_errors=True)

        # Осиротевшие каталоги (сервер перезапускали, cookie клиентов потеряны).
        try:
            entries = list(root.iterdir())
        except OSError:
            entries = []
        for path in entries:
            if not path.is_dir() or path.name in known:
                continue
            try:
                if now - path.stat().st_mtime < ORPHAN_TTL:
                    continue
                shutil.rmtree(path, ignore_errors=True)
            except OSError:
                continue

    def shutdown_all(self) -> None:
        """Останавливает все сессии при остановке сервера.

        Каталоги на диске сохраняются: если клиент вернётся с тем же cookie
        после рестарта, его файлы останутся на месте.
        """
        with self._lock:
            states = list(self._states.values())
            self._states.clear()
            self._last_seen.clear()
        for state in states:
            try:
                state.shutdown()
            except Exception:  # noqa: BLE001
                pass


async def sweep_loop(registry: SessionRegistry) -> None:
    """Периодическая уборка (в фоне lifespan сервера)."""
    while True:
        await asyncio.sleep(SWEEP_INTERVAL)
        try:
            registry.sweep()
        except Exception:  # noqa: BLE001 — уборка не должна ронять сервер
            pass


class SessionMiddleware:
    """ASGI-middleware: определяет sid и кладёт его в scope["state"].

    Чистый ASGI (без BaseHTTPMiddleware) — не буферизует SSE-поток /api/events.
    """

    def __init__(self, app: Callable, registry: SessionRegistry) -> None:
        self.app = app
        self.registry = registry

    async def __call__(
        self,
        scope: MutableMapping[str, Any],
        receive: Callable,
        send: Callable,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        sid: str | None = None
        generated = False

        raw_header = headers.get(SESSION_HEADER)
        if raw_header is not None:
            # Явный заголовок от API-клиента: молча подменять на другой id
            # нельзя (клиент потеряет состояние) — отвечаем 400.
            if not SESSION_ID_RE.match(raw_header):
                await self._reject(send)
                return
            sid = raw_header
        if sid is None:
            sid = _query_session_id(scope.get("query_string", b""))
        if sid is None:
            sid = _cookie_session_id(headers.get("cookie"))
        if sid is None:
            sid = self.registry.new_id()
            generated = True

        scope.setdefault("state", {})["session_id"] = sid

        if generated:
            cookie = f"{COOKIE_NAME}={sid}; Path=/; SameSite=Lax; HttpOnly"

            async def send_with_cookie(message: MutableMapping[str, Any]) -> None:
                if message["type"] == "http.response.start":
                    message.setdefault("headers", []).append(
                        (b"set-cookie", cookie.encode("latin-1"))
                    )
                await send(message)

            await self.app(scope, receive, send_with_cookie)
        else:
            await self.app(scope, receive, send)

    @staticmethod
    async def _reject(send: Callable) -> None:
        body = json.dumps(
            {"detail": f"Некорректный заголовок {SESSION_HEADER}"},
            ensure_ascii=False,
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 400,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode("latin-1")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
