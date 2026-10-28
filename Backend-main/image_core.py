"""Service Layer — Image Processing Core (Pillow). Ресэмплинг + сжатие."""
from __future__ import annotations

from pathlib import Path

from PIL import Image

# ТЗ п.2.1: три метода интерполяции, Lanczos — по умолчанию
_RESAMPLING = getattr(Image, "Resampling", Image)  # Pillow>=9.1
INTERPOLATION_MAP = {
    "Bilinear": _RESAMPLING.BILINEAR,
    "Bicubic": _RESAMPLING.BICUBIC,
    "Lanczos": _RESAMPLING.LANCZOS,
}

OUTPUT_EXT = {
    "WebP": ".webp",
    "JPEG": ".jpg",
    "PNG": ".png",
    "BMP": ".bmp",
    "ICO": ".ico",
    "GIF": ".gif",
    "TIFF": ".tiff",
    "AVIF": ".avif",
}

# Имя кодека Pillow для каждого выходного формата
_PIL_FORMAT = {
    "WebP": "WEBP",
    "JPEG": "JPEG",
    "PNG": "PNG",
    "BMP": "BMP",
    "ICO": "ICO",
    "GIF": "GIF",
    "TIFF": "TIFF",
    "AVIF": "AVIF",
}

# Форматы, где качество (10..100) реально влияет на сжатие
_QUALITY_FORMATS = {"WebP", "JPEG", "AVIF"}

# Режимы изображения, которые Pillow гарантированно принимает при записи
_SAFE_MODES: dict[str, tuple[str, ...]] = {
    "BMP": ("1", "L", "P", "RGB"),
    "GIF": ("P", "L", "RGB", "RGBA"),
    "PNG": ("1", "L", "LA", "P", "RGB", "RGBA", "I", "I;16"),
    "ICO": ("RGB", "RGBA"),
    "TIFF": ("1", "L", "LA", "P", "RGB", "RGBA", "CMYK", "I", "I;16"),
    "AVIF": ("L", "RGB", "RGBA"),
}


def available_output_formats() -> list[str]:
    """Форматы, запись которых реально поддерживается установленным Pillow.

    AVIF доступен только при установленном pillow-avif-plugin.
    """
    Image.init()
    return [name for name, pil in _PIL_FORMAT.items() if pil in Image.SAVE]


def compute_target_size(
    orig_w: int,
    orig_h: int,
    req_w: int | None,
    req_h: int | None,
    keep_aspect: bool,
) -> tuple[int, int]:
    """Пересчёт размера с сохранением пропорций (aspect ratio) — ТЗ п.3."""
    if not req_w and not req_h:
        return orig_w, orig_h
    if keep_aspect:
        if req_w and req_h:
            scale = min(req_w / orig_w, req_h / orig_h)
            return max(1, int(orig_w * scale)), max(1, int(orig_h * scale))
        if req_w:
            scale = req_w / orig_w
            return req_w, max(1, int(orig_h * scale))
        scale = req_h / orig_h  # type: ignore[operator]
        return max(1, int(orig_w * scale)), req_h  # type: ignore[return-value]
    return int(req_w or orig_w), int(req_h or orig_h)


def _fit_mode(im: Image.Image, pil_format: str) -> Image.Image:
    """Приводит режим изображения к допустимому для целевого формата."""
    allowed = _SAFE_MODES.get(pil_format)
    if not allowed or im.mode in allowed:
        return im
    has_alpha = "A" in im.getbands() or im.mode in ("PA", "LA", "P")
    target = "RGBA" if has_alpha and "RGBA" in allowed else "RGB"
    if target not in allowed:
        target = "RGB" if "RGB" in allowed else allowed[0]
    return im.convert(target)


def convert_image(
    src_path: str | Path,
    dst_dir: str | Path,
    output_format: str = "WebP",
    quality: int = 80,
    resize_enabled: bool = False,
    width: int | None = None,
    height: int | None = None,
    keep_aspect: bool = True,
    interpolation: str = "Lanczos",
) -> Path:
    """Конвертация одного файла. Бросает исключение при битом файле (caller логирует)."""
    src = Path(src_path)
    dst_dir = Path(dst_dir)
    dst_dir.mkdir(parents=True, exist_ok=True)

    fmt = output_format if output_format in OUTPUT_EXT else "WebP"
    pil_format = _PIL_FORMAT[fmt]
    Image.init()
    if pil_format not in Image.SAVE:
        hint = " (для AVIF установите pillow-avif-plugin)" if fmt == "AVIF" else ""
        raise ValueError(f"Pillow не поддерживает запись формата {fmt}{hint}")

    resample = INTERPOLATION_MAP.get(interpolation, Image.LANCZOS)
    quality = max(10, min(100, int(quality)))

    with Image.open(src) as im:
        im.load()  # раннее выявление битых файлов
        orig_w, orig_h = im.size

        if resize_enabled:
            tw, th = compute_target_size(orig_w, orig_h, width, height, keep_aspect)
            if (tw, th) != (orig_w, orig_h):
                im = im.resize((tw, th), resample=resample)

        ext = OUTPUT_EXT[fmt]
        dst = dst_dir / (src.stem + ext)
        # Избегаем коллизий имён при пакетной обработке
        i = 1
        while dst.exists():
            dst = dst_dir / f"{src.stem}_{i}{ext}"
            i += 1

        save_kwargs: dict = {}
        if fmt in _QUALITY_FORMATS:
            save_kwargs["quality"] = quality

        if fmt == "WebP":
            # RGBA сохраняется корректно; метод 6 = лучшее сжатие
            save_kwargs.update({"method": 6})
            if im.mode == "P":
                im = im.convert("RGBA")
            im.save(dst, "WEBP", **save_kwargs)
        elif fmt == "JPEG":
            # JPEG — без альфа-канала; альфа (в т.ч. прозрачность палитры)
            # композится на белый фон, чтобы не получить чёрные провалы
            if im.mode == "P":
                im = im.convert("RGBA")
            if im.mode in ("RGBA", "LA", "PA"):
                bg = Image.new("RGB", im.size, (255, 255, 255))
                alpha = im.convert("RGBA").split()[-1]
                bg.paste(im.convert("RGB"), mask=alpha)
                im = bg
            elif im.mode != "RGB":
                im = im.convert("RGB")
            im.save(dst, "JPEG", optimize=True, **save_kwargs)
        else:
            # PNG/BMP/ICO/GIF/TIFF/AVIF: приводим режим и пишем напрямую
            im = _fit_mode(im, pil_format)
            im.save(dst, pil_format, **save_kwargs)
        return dst
