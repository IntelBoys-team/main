
"""PixelForge: подключение улучшения к API и веб-интерфейсу.

Запуск из корня backend:
    python apply_pixelforge_fix.py
"""
from __future__ import annotations

from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent

PATCHES = {
    "config_manager.py": [
        (
            '"quality": 80,',
            '"quality": 95,',
        ),
        (
            '    "keep_aspect": True,\n}',
            '    "keep_aspect": True,\n'
            '    "enhance_enabled": True,\n'
            '    "enhance_strength": 1.0,\n'
            '    "lossless_webp": False,\n}',
        ),
        (
            'int(self.data.get("quality", 80))',
            'int(self.data.get("quality", DEFAULTS["quality"]))',
        ),
        (
            'self.data["quality"] = 80',
            'self.data["quality"] = DEFAULTS["quality"]',
        ),
        (
            '        if self.data.get("output_format") not in ALLOWED_FORMATS:',
            '        try:\n'
            '            self.data["enhance_strength"] = max(0.0, min(2.0, float(self.data.get("enhance_strength", 1.0))))\n'
            '        except (TypeError, ValueError):\n'
            '            self.data["enhance_strength"] = 1.0\n'
            '        for key in ("enhance_enabled", "lossless_webp"):\n'
            '            if not isinstance(self.data.get(key), bool):\n'
            '                self.data[key] = DEFAULTS[key]\n'
            '        if self.data.get("output_format") not in ALLOWED_FORMATS:',
        ),
    ],

    "api/app.py": [
        (
            'quality: int = Field(80,',
            'quality: int = Field(95,',
        ),
        (
            '    keep_aspect: bool = True\n    output_dir:',
            '    keep_aspect: bool = True\n'
            '    enhance_enabled: bool = True\n'
            '    enhance_strength: float = Field(1.0, ge=0.0, le=2.0)\n'
            '    lossless_webp: bool = False\n'
            '    output_dir:',
        ),
        (
            '    keep_aspect: bool | None = None\n    output_dir:',
            '    keep_aspect: bool | None = None\n'
            '    enhance_enabled: bool | None = None\n'
            '    enhance_strength: float | None = Field(None, ge=0.0, le=2.0)\n'
            '    lossless_webp: bool | None = None\n'
            '    output_dir:',
        ),
    ],

    "api/state.py": [
        (
            '                "keep_aspect": settings.keep_aspect,\n'
            '                "output_dir": settings.output_dir or "",',
            '                "keep_aspect": settings.keep_aspect,\n'
            '                "enhance_enabled": settings.enhance_enabled,\n'
            '                "enhance_strength": settings.enhance_strength,\n'
            '                "lossless_webp": settings.lossless_webp,\n'
            '                "output_dir": settings.output_dir or "",',
        ),
        (
            '            "keep_aspect": settings.keep_aspect,\n'
            '        }',
            '            "keep_aspect": settings.keep_aspect,\n'
            '            "enhance_enabled": settings.enhance_enabled,\n'
            '            "enhance_strength": settings.enhance_strength,\n'
            '            "lossless_webp": settings.lossless_webp,\n'
            '        }',
        ),
        (
            '            f"интерполяция={kwargs[\'interpolation\']} файлов={len(files)}"',
            '            f"интерполяция={kwargs[\'interpolation\']} "\n'
            '            f"улучшение={\'вкл\' if kwargs[\'enhance_enabled\'] else \'выкл\'} "\n'
            '            f"сила={kwargs[\'enhance_strength\']:.1f} "\n'
            '            f"WebP_без_потерь={kwargs[\'lossless_webp\']} файлов={len(files)}"',
        ),
    ],

    "desktop_ui/index.html": [
        (
            'id="qualityValue">80%',
            'id="qualityValue">95%',
        ),
        (
            'id="quality" min="10" max="100" step="1" value="80"',
            'id="quality" min="10" max="100" step="1" value="95"',
        ),
        (
            '        <label class="check">\n'
            '          <input type="checkbox" id="resizeEnabled">',
            '        <label class="check">\n'
            '          <input type="checkbox" id="enhanceEnabled" checked>\n'
            '          <span>Улучшать чёткость и цвета</span>\n'
            '        </label>\n'
            '        <div class="line">\n'
            '          <label class="field-label" for="enhanceStrength">Сила улучшения</label>\n'
            '          <span class="value" id="enhanceStrengthValue">1.0×</span>\n'
            '        </div>\n'
            '        <input type="range" class="range" id="enhanceStrength" min="0" max="2" step="0.1" value="1">\n'
            '        <label class="check">\n'
            '          <input type="checkbox" id="losslessWebp">\n'
            '          <span>WebP без потерь (больше размер)</span>\n'
            '        </label>\n\n'
            '        <label class="check">\n'
            '          <input type="checkbox" id="resizeEnabled">',
        ),
    ],

    "desktop_ui/app.js": [
        (
            '    qualityValue: $("qualityValue"),',
            '    qualityValue: $("qualityValue"),\n'
            '    enhanceEnabled: $("enhanceEnabled"),\n'
            '    enhanceStrength: $("enhanceStrength"),\n'
            '    enhanceStrengthValue: $("enhanceStrengthValue"),\n'
            '    losslessWebp: $("losslessWebp"),',
        ),
        (
            '      quality: Number(el.quality.value),',
            '      quality: Number(el.quality.value),\n'
            '      enhance_enabled: el.enhanceEnabled.checked,\n'
            '      enhance_strength: Number(el.enhanceStrength.value),\n'
            '      lossless_webp: el.losslessWebp.checked,',
        ),
        (
            '    el.quality.value = String(cfg.quality ?? 80);\n'
            '    el.qualityValue.textContent = `${el.quality.value}%`;',
            '    el.quality.value = String(cfg.quality ?? 95);\n'
            '    el.qualityValue.textContent = `${el.quality.value}%`;\n'
            '    el.enhanceEnabled.checked = cfg.enhance_enabled !== false;\n'
            '    el.enhanceStrength.value = String(cfg.enhance_strength ?? 1);\n'
            '    el.enhanceStrengthValue.textContent = `${Number(el.enhanceStrength.value).toFixed(1)}×`;\n'
            '    el.losslessWebp.checked = Boolean(cfg.lossless_webp);',
        ),
        (
            '      el.quality,\n      el.resizeEnabled,',
            '      el.quality,\n'
            '      el.enhanceEnabled,\n'
            '      el.enhanceStrength,\n'
            '      el.losslessWebp,\n'
            '      el.resizeEnabled,',
        ),
        (
            '    if (!busy) updateDimsEnabled();\n  }',
            '    if (!busy) updateDimsEnabled();\n'
            '    el.enhanceStrength.disabled = busy || !el.enhanceEnabled.checked;\n'
            '    el.losslessWebp.disabled = busy || el.format.value !== "WebP";\n'
            '  }',
        ),
        (
            '    el.interp.addEventListener("change", schedulePersist);',
            '    el.interp.addEventListener("change", schedulePersist);\n'
            '    el.format.addEventListener("change", () => {\n'
            '      el.losslessWebp.disabled = el.format.value !== "WebP";\n'
            '    });\n'
            '    el.enhanceEnabled.addEventListener("change", () => {\n'
            '      el.enhanceStrength.disabled = !el.enhanceEnabled.checked;\n'
            '      schedulePersist();\n'
            '    });\n'
            '    el.enhanceStrength.addEventListener("input", () => {\n'
            '      el.enhanceStrengthValue.textContent = `${Number(el.enhanceStrength.value).toFixed(1)}×`;\n'
            '      schedulePersist();\n'
            '    });\n'
            '    el.losslessWebp.addEventListener("change", schedulePersist);',
        ),
    ],

    "desktop_ui/styles.css": [
        (
            '  gap: 6px;\n  overflow: hidden;\n}',
            '  gap: 6px;\n'
            '  overflow-y: auto;\n'
            '  overflow-x: hidden;\n}',
        ),
    ],
}


def main() -> None:
    staged: dict[Path, str] = {}

    # Сначала проверяем все файлы, ничего не изменяя.
    for filename, edits in PATCHES.items():
        path = ROOT / filename

        if not path.is_file():
            raise FileNotFoundError(
                f"Не найден файл: {path}"
            )

        old_text = path.read_text(encoding="utf-8")
        new_text = old_text

        for old, new in edits:
            # Если изменение уже применено, пропускаем.
            if new in new_text:
                continue

            if old not in new_text:
                raise RuntimeError(
                    f"Неизвестная версия {filename}, "
                    f"нет фрагмента: {old[:70]!r}"
                )

            if new_text.count(old) != 1:
                raise RuntimeError(
                    f"Неоднозначный участок в {filename}: "
                    f"{old[:70]!r}"
                )

            new_text = new_text.replace(
                old,
                new,
                1,
            )

        staged[path] = new_text

    # Записываем только после успешной проверки.
    for path, new_text in staged.items():
        original = path.read_text(encoding="utf-8")

        if original != new_text:
            backup = path.with_name(
                path.name + ".bak"
            )

            if not backup.exists():
                shutil.copy2(path, backup)

            path.write_text(
                new_text,
                encoding="utf-8",
            )

            print(
                f"OK: {path.relative_to(ROOT)} "
                f"(резервная копия {backup.name})"
            )
        else:
            print(
                f"Уже исправлен: {path.relative_to(ROOT)}"
            )

    print(
        "Готово. Перезапустите FastAPI "
        "и обновите страницу Ctrl+F5."
    )


if __name__ == "__main__":
    main()
