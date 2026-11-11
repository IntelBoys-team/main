"""Service Layer — Archive Validator: потоковое чтение + сквозная валидация Magic Numbers."""
from __future__ import annotations

import os
import shutil
import zipfile
from pathlib import Path
from typing import Callable

MAGIC_PNG = b"\x89PNG"
MAGIC_JPEG = b"\xff\xd8\xff"
MAGIC_BMP = b"BM"
MAGIC_GIF = b"GIF8"
MAGIC_ICO = b"\x00\x00\x01\x00"
MAGIC_TIFF_LE = b"II*\x00"
MAGIC_TIFF_BE = b"MM\x00*"
MAGIC_RIFF = b"RIFF"
MAGIC_WEBP = b"WEBP"
MAGIC_FTYP = b"ftyp"
MAGIC_AVIF = b"avif"

# Входные форматы (что можно загружать)
SUPPORTED_EXTS = {
    ".png", ".jpg", ".jpeg", ".bmp",
    ".webp", ".gif", ".ico", ".tif", ".tiff",
    ".avif",
}
# Выходные форматы (во что можно конвертировать)
OUTPUT_FORMATS = {"WebP", "JPEG", "PNG", "BMP", "ICO", "GIF", "TIFF", "AVIF"}
ARCHIVE_EXTS = {".zip", ".rar", ".7z"}

# Сколько байт читаем для проверки сигнатуры (WebP/AVIF требуют 12)
HEADER_LEN = 12

VALIDATION_ERROR_MSG = (
    "Ошибка валидации: архив содержит неподдерживаемые типы данных. "
    "Конвертация невозможна"
)


class ValidationError(Exception):
    pass


# ---------------------------------------------------------------- RAR-инструмент
# rarfile только читает заголовки сам, а распаковку отдаёт внешнему
# unrar/7z. На Windows 7-Zip обычно установлен, но не добавлен в PATH,
# поэтому ищем его в типовых местах и подставляем в rarfile вручную.
_RAR_TOOL_READY = False


def _rar_tool_candidates() -> list[tuple[str, Path]]:
    """(имя настройки rarfile, путь) — кандидаты на внешний инструмент."""
    sevenzip: list[Path] = []
    for env in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        base = os.environ.get(env)
        if base:
            sevenzip.append(Path(base) / "7-Zip" / "7z.exe")
    candidates = [("SEVENZIP_TOOL", p) for p in sevenzip]
    # bsdtar в Windows называется tar.exe (libarchive читает RAR)
    candidates.append(
        ("BSDTAR_TOOL", Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe")
    )
    return candidates


def ensure_rar_tool() -> None:
    """Гарантирует рабочий инструмент для rarfile (иначе .rar → ValidationError)."""
    global _RAR_TOOL_READY
    if _RAR_TOOL_READY:
        return
    try:
        import rarfile
    except ImportError:
        return
    try:
        rarfile.tool_setup()
        _RAR_TOOL_READY = True
        return
    except rarfile.RarCannotExec:
        pass
    for setting, candidate in _rar_tool_candidates():
        if not candidate.is_file():
            continue
        setattr(rarfile, setting, str(candidate))
        rarfile.CURRENT_SETUP = None
        try:
            rarfile.tool_setup()
            _RAR_TOOL_READY = True
            return
        except rarfile.RarCannotExec:
            continue
    rarfile.CURRENT_SETUP = None


def detect_image_kind(header: bytes) -> str | None:
    if header.startswith(MAGIC_PNG):
        return "png"
    if header.startswith(MAGIC_JPEG):
        return "jpeg"
    if header.startswith(MAGIC_BMP):
        return "bmp"
    if header.startswith(MAGIC_GIF):
        return "gif"
    if header.startswith(MAGIC_ICO):
        return "ico"
    if header.startswith(MAGIC_TIFF_LE) or header.startswith(MAGIC_TIFF_BE):
        return "tiff"
    if len(header) >= 12 and header[:4] == MAGIC_RIFF and header[8:12] == MAGIC_WEBP:
        return "webp"
    if len(header) >= 12 and header[4:8] == MAGIC_FTYP and header[8:12] == MAGIC_AVIF:
        return "avif"
    return None


def _is_supported_name(name: str) -> bool:
    return Path(name).suffix.lower() in SUPPORTED_EXTS


def iter_zip_members(archive: Path):
    """Потоковое чтение оглавления без полной распаковки — ТЗ п.2.3."""
    with zipfile.ZipFile(archive, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            yield info.filename


def read_zip_header(archive: Path, member: str, n: int = HEADER_LEN) -> bytes:
    with zipfile.ZipFile(archive, "r") as zf:
        with zf.open(member, "r") as f:
            return f.read(n)


def _validate_member_list(
    members: list[str], read_header: Callable[[str], bytes] | None = None
) -> None:
    """Сквозная валидация: расширение + (если задан read_header) бинарная сигнатура.

    read_header=None — только проверка расширений (используется для 7z: py7zr
    не умеет читать члены архива в память, сигнатуры проверяются после
    распаковки, см. финальный чек в validate_and_extract).
    """
    if not members:
        raise ValidationError(VALIDATION_ERROR_MSG + " (архив пуст)")
    bad: list[str] = []
    for name in members:
        if not _is_supported_name(name):
            bad.append(name)
            continue
        if read_header is None:
            continue
        try:
            header = read_header(name)
        except Exception:
            bad.append(name + " (не читается)")
            continue
        if detect_image_kind(header) is None:
            bad.append(name + " (сигнатура не совпала)")
    if bad:
        preview = ", ".join(bad[:5])
        raise ValidationError(f"{VALIDATION_ERROR_MSG}: {preview}")


def validate_and_extract(archive: Path, cache_dir: Path) -> list[Path]:
    """Валидация + безопасная распаковка в кэш. При ошибке кэш стирается."""
    archive = Path(archive)
    cache_dir = Path(cache_dir)
    suffix = archive.suffix.lower()

    try:
        if suffix == ".zip":
            members = [m for m in iter_zip_members(archive)]
            _validate_member_list(members, lambda m: read_zip_header(archive, m))
            cache_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(archive, "r") as zf:
                zf.extractall(cache_dir)
        elif suffix == ".7z":
            try:
                import py7zr
            except ImportError as e:
                raise ValidationError("Для .7z установите зависимость py7zr") from e
            with py7zr.SevenZipFile(archive, "r") as zf:
                if zf.needs_password():
                    raise ValidationError(VALIDATION_ERROR_MSG + " (архив защищён паролем)")
                # getnames() разбирает заголовок архива: битый заголовок = ошибка здесь
                names = zf.getnames()
                members = [n for n in names if not n.endswith("/")]
                # py7zr (>=1.0) не предоставляет чтения членов в память —
                # расширения проверяем по оглавлению, а сигнатуры — после
                # распаковки (финальный чек ниже ловит и переименованные файлы)
                _validate_member_list(members)
                cache_dir.mkdir(parents=True, exist_ok=True)
                # Повреждённый LZMA-поток бросает lzma.LZMAError прямо здесь
                zf.extractall(cache_dir)
        elif suffix == ".rar":
            try:
                import rarfile
            except ImportError as e:
                raise ValidationError("Для .rar установите зависимость rarfile") from e
            ensure_rar_tool()
            with rarfile.RarFile(archive, "r") as rf:
                members = [i.filename for i in rf.infolist() if not i.is_dir()]
                def _rh2(n: str) -> bytes:
                    with rf.open(n) as f:
                        return f.read(HEADER_LEN)
                _validate_member_list(members, _rh2)
                cache_dir.mkdir(parents=True, exist_ok=True)
                rf.extractall(cache_dir)
        else:
            raise ValidationError(f"Неподдерживаемый тип архива: {suffix}")
    except ValidationError:
        shutil.rmtree(cache_dir, ignore_errors=True)
        raise
    except zipfile.BadZipFile as e:
        shutil.rmtree(cache_dir, ignore_errors=True)
        raise ValidationError(f"{VALIDATION_ERROR_MSG} (архив повреждён)") from e
    except Exception as e:
        # rarfile.NotRarFile, lzma.LZMAError, ошибки пароля и прочие
        # «сырые» исключения → валидационная ошибка, а не HTTP 500 у API
        shutil.rmtree(cache_dir, ignore_errors=True)
        raise ValidationError(f"{VALIDATION_ERROR_MSG} (архив повреждён: {e})") from e

    # Собрать извлечённые изображения
    out = [p for p in cache_dir.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS]
    # Финальная проверка сигнатур на диске (защита от переименованных файлов)
    for p in list(out):
        try:
            if detect_image_kind(p.read_bytes()[:HEADER_LEN]) is None:
                shutil.rmtree(cache_dir, ignore_errors=True)
                raise ValidationError(f"{VALIDATION_ERROR_MSG}: {p.name}")
        except OSError:
            pass
    if not out:
        shutil.rmtree(cache_dir, ignore_errors=True)
        raise ValidationError(VALIDATION_ERROR_MSG + " (изображения не найдены)")
    return sorted(out)


def validate_single_image(path: Path) -> Path:
    """Валидация одиночного файла (расширение + Magic Number) — ТЗ п.2.3."""
    path = Path(path)
    if not path.is_file():
        raise ValidationError(f"Файл не найден: {path}")
    if path.suffix.lower() not in SUPPORTED_EXTS:
        raise ValidationError(VALIDATION_ERROR_MSG)
    try:
        header = path.read_bytes()[:HEADER_LEN]
    except OSError as e:
        raise ValidationError(f"{VALIDATION_ERROR_MSG}: {path.name} не читается") from e
    if detect_image_kind(header) is None:
        raise ValidationError(f"{VALIDATION_ERROR_MSG}: {path.name}")
    return path


def collect_from_folder(folder: Path) -> list[Path]:
    """Сбор изображений из папки (рекурсивно). Неподдерживаемые пропускаются."""
    folder = Path(folder)
    return sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS)


def is_archive(path: Path) -> bool:
    return Path(path).suffix.lower() in ARCHIVE_EXTS
