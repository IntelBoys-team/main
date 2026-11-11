
"""PixelForge — обработка изображений: масштабирование, улучшение, конвертация."""
from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter, ImageOps


_RESAMPLING = getattr(Image, "Resampling", Image)

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

_PIL_FORMAT = {
    name: {"WebP": "WEBP", "JPEG": "JPEG"}.get(name, name)
    for name in OUTPUT_EXT
}

_QUALITY_FORMATS = {"WebP", "JPEG", "AVIF"}

_SAFE_MODES = {
    "WEBP": ("RGB", "RGBA"),
    "PNG": ("1", "L", "LA", "P", "RGB", "RGBA", "I", "I;16"),
    "BMP": ("1", "L", "P", "RGB"),
    "ICO": ("RGB", "RGBA"),
    "GIF": ("L", "P", "RGB", "RGBA"),
    "TIFF": ("1", "L", "LA", "P", "RGB", "RGBA", "CMYK", "I", "I;16"),
    "AVIF": ("L", "RGB", "RGBA"),
}

_DEST_LOCK = threading.Lock()
_RESERVED_DESTINATIONS: set[Path] = set()


def available_output_formats() -> list[str]:
    """Возвращает форматы, поддерживаемые текущей сборкой Pillow."""
    Image.init()
    return [
        name
        for name, encoder in _PIL_FORMAT.items()
        if encoder in Image.SAVE
    ]


def compute_target_size(
    orig_w: int,
    orig_h: int,
    req_w: int | None,
    req_h: int | None,
    keep_aspect: bool,
) -> tuple[int, int]:
    """Вычисляет разрешение без обрезки изображения."""
    if orig_w <= 0 or orig_h <= 0:
        raise ValueError("Исходные размеры должны быть положительными")

    if req_w is not None and req_w <= 0:
        raise ValueError("Ширина должна быть положительной")

    if req_h is not None and req_h <= 0:
        raise ValueError("Высота должна быть положительной")

    if req_w is None and req_h is None:
        return orig_w, orig_h

    if keep_aspect:
        if req_w is not None and req_h is not None:
            factor = min(req_w / orig_w, req_h / orig_h)
            return (
                max(1, round(orig_w * factor)),
                max(1, round(orig_h * factor)),
            )

        if req_w is not None:
            return req_w, max(1, round(orig_h * req_w / orig_w))

        assert req_h is not None
        return max(1, round(orig_w * req_h / orig_h)), req_h

    return (
        req_w if req_w is not None else orig_w,
        req_h if req_h is not None else orig_h,
    )


def _has_alpha(image: Image.Image) -> bool:
    return (
        "A" in image.getbands()
        or "transparency" in image.info
    )


def _fit_mode(image: Image.Image, encoder: str) -> Image.Image:
    """Преобразует изображение в режим, поддерживаемый кодеком."""
    permitted = _SAFE_MODES.get(encoder)

    if not permitted or image.mode in permitted:
        return image

    target = (
        "RGBA"
        if _has_alpha(image) and "RGBA" in permitted
        else "RGB"
    )

    if target not in permitted:
        target = "RGB" if "RGB" in permitted else permitted[0]

    return image.convert(target)


def _enhance_image(
    image: Image.Image,
    strength: float,
) -> Image.Image:
    """Улучшает контраст, насыщенность и резкость.

    Альфа-канал сохраняется. 16-битные изображения не переводятся
    в 8-битные ради фильтра.
    """
    if not 0 <= strength <= 2:
        raise ValueError("Сила улучшения должна быть от 0 до 2")

    if strength == 0 or image.mode in ("I;16", "I", "F"):
        return image

    if image.mode not in ("RGB", "RGBA", "L", "LA"):
        image = image.convert(
            "RGBA" if _has_alpha(image) else "RGB"
        )

    alpha = (
        image.getchannel("A")
        if "A" in image.getbands()
        else None
    )

    if image.mode == "RGBA":
        color = image.convert("RGB")
    elif image.mode == "LA":
        color = image.getchannel("L")
    else:
        color = image

    # Умеренная коррекция контраста.
    color = ImageEnhance.Contrast(color).enhance(
        1 + 0.055 * strength
    )

    # Небольшое усиление насыщенности.
    if color.mode == "RGB":
        color = ImageEnhance.Color(color).enhance(
            1 + 0.035 * strength
        )

    # Нерезкая маска: подчёркивает границы и текстуры.
    color = color.filter(
        ImageFilter.UnsharpMask(
            radius=1.2,
            percent=round(115 * strength),
            threshold=3,
        )
    )

    if alpha is not None:
        color.putalpha(alpha)

    return color


def _flatten_for_jpeg(image: Image.Image) -> Image.Image:
    """Заменяет прозрачность белым фоном при сохранении JPEG."""
    if _has_alpha(image):
        rgba = image.convert("RGBA")
        bg = Image.new(
            "RGBA",
            rgba.size,
            (255, 255, 255, 255),
        )
        return Image.alpha_composite(bg, rgba).convert("RGB")

    return image if image.mode == "RGB" else image.convert("RGB")


def _reserve_destination(
    out_dir: Path,
    stem: str,
    extension: str,
) -> Path:
    """Защищает от совпадения имён при параллельной конвертации."""
    with _DEST_LOCK:
        index = 0

        while True:
            suffix = f"_{index}" if index else ""
            candidate = out_dir / f"{stem}{suffix}{extension}"

            if (
                not candidate.exists()
                and candidate not in _RESERVED_DESTINATIONS
            ):
                _RESERVED_DESTINATIONS.add(candidate)
                return candidate

            index += 1


def convert_image(
    src_path: str | Path,
    dst_dir: str | Path,
    output_format: str = "WebP",
    quality: int = 95,
    resize_enabled: bool = False,
    width: int | None = None,
    height: int | None = None,
    keep_aspect: bool = True,
    interpolation: str = "Lanczos",
    enhance_enabled: bool = True,
    enhance_strength: float = 1.0,
    lossless_webp: bool = False,
) -> Path:
    """Конвертирует и улучшает изображение.

    Улучшение выполняется даже при resize_enabled=False.
    Обрабатывается первый кадр анимации или TIFF-документа.
    """
    src = Path(src_path)
    out_dir = Path(dst_dir)

    fmt = (
        output_format
        if output_format in OUTPUT_EXT
        else "WebP"
    )

    encoder = _PIL_FORMAT[fmt]

    if fmt not in available_output_formats():
        extra = (
            " (проверьте поддержку AVIF в Pillow)"
            if fmt == "AVIF"
            else ""
        )
        raise ValueError(
            f"Pillow не поддерживает сохранение {fmt}{extra}"
        )

    if interpolation not in INTERPOLATION_MAP:
        raise ValueError(
            f"Неизвестная интерполяция: {interpolation}"
        )

    quality = max(10, min(100, int(quality)))
    enhance_strength = float(enhance_strength)

    if not 0 <= enhance_strength <= 2:
        raise ValueError(
            "Сила улучшения должна быть от 0 до 2"
        )

    with Image.open(src) as opened:
        opened.seek(0)
        opened.load()

        # Учитываем поворот фотографии из EXIF.
        image = ImageOps.exif_transpose(opened)

        icc_profile = image.info.get("icc_profile")

        # Масштабирование.
        if resize_enabled:
            target = compute_target_size(
                *image.size,
                width,
                height,
                keep_aspect,
            )

            if target != image.size:
                image = image.resize(
                    target,
                    resample=INTERPOLATION_MAP[interpolation],
                )

        # Реальное визуальное улучшение изображения.
        if enhance_enabled:
            image = _enhance_image(
                image,
                enhance_strength,
            )

        kwargs: dict = {}

        if fmt in _QUALITY_FORMATS:
            kwargs["quality"] = quality

        # По возможности сохраняем цветовой профиль.
        if icc_profile and fmt in (
            "WebP", "JPEG", "PNG", "TIFF", "AVIF"
        ):
            kwargs["icc_profile"] = icc_profile

        if fmt == "JPEG":
            image = _flatten_for_jpeg(image)
            kwargs.update(
                optimize=True,
                subsampling=0,
            )

        elif fmt == "WebP":
            image = _fit_mode(image, "WEBP")
            kwargs.update(
                method=6,
                lossless=bool(lossless_webp),
            )

        elif fmt == "PNG":
            image = _fit_mode(image, "PNG")
            kwargs["optimize"] = True

        elif fmt == "ICO":
            image = _fit_mode(image, "ICO")

            if max(image.size) > 256:
                size = compute_target_size(
                    *image.size,
                    256,
                    256,
                    True,
                )
                image = image.resize(
                    size,
                    _RESAMPLING.LANCZOS,
                )

            kwargs["sizes"] = [image.size]

        elif fmt == "BMP":
            image = _flatten_for_jpeg(image)

        else:
            image = _fit_mode(image, encoder)

            if fmt == "TIFF":
                kwargs["compression"] = "tiff_adobe_deflate"

        out_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        destination = _reserve_destination(
            out_dir,
            src.stem,
            OUTPUT_EXT[fmt],
        )

        scratch: Path | None = None

        try:
            # Временный файл, затем атомарное перемещение.
            fd, name = tempfile.mkstemp(
                prefix=".pixelforge-",
                suffix=OUTPUT_EXT[fmt],
                dir=out_dir,
            )

            scratch = Path(name)
            os.close(fd)

            image.save(
                scratch,
                encoder,
                **kwargs,
            )

            os.replace(
                scratch,
                destination,
            )

            scratch = None
            return destination

        finally:
            if scratch is not None:
                scratch.unlink(missing_ok=True)

            with _DEST_LOCK:
                _RESERVED_DESTINATIONS.discard(
                    destination
                )
