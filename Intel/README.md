# PixelForge — пакетный конвертер изображений (ТЗ intelboy)

3-Tier Desktop: Presentation (`ui_pyqt.py`, PyQt6) / Service (`thread_manager.py`, `archive_validator.py`, `image_core.py`) / Data (`config_manager.py`).

## Запуск
```bat
python -m pip install -r requirements.txt
python main.py
```

## Соответствие ТЗ
- Выход: WebP (default) / JPEG, качество 10–100 (default 80), интерполяция Lanczos (default) / Bicubic / Bilinear (Pillow LANCZOS/BICUBIC/BILINEAR).
- Ресайз с aspect ratio, ручной W/H.
- Пул потоков = `os.cpu_count()` (2–16), FIFO `queue.Queue`, счётчик `counter++` под `threading.Lock` (Mutex).
- Архивы `.zip` (+ `.7z`/`.rar` при py7zr/rarfile): потоковое чтение оглавления, валидация расширения + Magic Numbers (PNG `89 50 4E 47`, JPEG `FF D8 FF`, BMP `42 4D`). Чужой файл → `ValidationError` «Ошибка валидации… Конвертация невозможна», модальное окно, красная рамка, кэш стёрт.
- Кэш `.cache_processing/` в системном temp, стирается после цикла/аборта. Конфиг `config.json` в `%APPDATA%/PixelForge` (fallback — cwd).
- UI 800×600 Dark Mode `#1E1E1E`/`#2D2D2D`, акцент `#007ACC`, ошибка `#D32F2F`. Drag-and-Drop нативный Qt (окно + зона) + кнопки Обзор. Старт блокирован до валидации; при конвертации настройки заморожены; прогресс «Обработано: X из Y (Z%)»; битые файлы — в лог без падения; без звуковых сигналов.
```
