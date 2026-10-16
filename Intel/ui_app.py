"""Presentation Layer / UI — Single-Page 800x600 Dark Mode. Только сигналы в Service Layer."""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path

try:
    import customtkinter as ctk
    HAS_CTK = True
except ImportError:
    HAS_CTK = False

from config_manager import ConfigManager, get_cache_dir
from archive_validator import (
    collect_from_folder, is_archive, validate_and_extract,
    ValidationError, VALIDATION_ERROR_MSG,
)
from thread_manager import ThreadManager

# Палитра — ТЗ п.5.1
BG = "#1E1E1E"
PANEL = "#2D2D2D"
TEXT_MAIN = "#FFFFFF"
TEXT_SUB = "#E0E0E0"
ACCENT = "#007ACC"
ERROR = "#D32F2F"
OK_GREEN = "#2E7D32"


class ImageConverterApp:
    def __init__(self):
        if not HAS_CTK:
            raise RuntimeError("Установите customtkinter: pip install -r requirements.txt")
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        # TkinterDnD для настоящей Drag-and-Drop зоны (опционально).
        # CTk + DnD: корень-наследник обоих классов, иначе CTk-виджеты в обычном Tk ломают темы.
        self._dnd_files = None
        try:
            from tkinterdnd2 import TkinterDnD, DND_FILES  # type: ignore

            class _DnDCTk(ctk.CTk, TkinterDnD.DnDWrapper):  # type: ignore
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, **kwargs)
                    self.TkdndVersion = TkinterDnD._require(self)

            self.root = _DnDCTk()
            self._dnd_files = DND_FILES
        except Exception:
            self.root = ctk.CTk()
            self._dnd_files = None

        self.root.title("PixelForge — Конвертер изображений")
        self.root.geometry("800x600")
        self.root.resizable(False, False)
        try:
            self.root.configure(fg_color=BG)
        except Exception:
            try:
                self.root.configure(bg=BG)
            except Exception:
                pass

        self.cfg = ConfigManager()
        self.tm = ThreadManager()
        self.source_files: list[Path] = []
        self.source_label_text = tk.StringVar(value="Перетащите сюда папку или архив (.zip, .rar, .7z) или нажмите «Обзор»")
        self.working = False
        self.cache_dir = get_cache_dir()
        self._status_var = tk.StringVar(value="Ожидание источника…")
        self._counter_var = tk.StringVar(value="Обработано: 0 из 0 (0%)")

        self._build()
        self._apply_config_to_widgets()
        self._set_start_enabled(False)

    # ---------- построение ----------
    def _build(self):
        # Верхняя зона: Drag-and-Drop (width/height — только в конструктор, ТЗ п.5.2)
        self.drop_frame = ctk.CTkFrame(self.root, fg_color=PANEL, border_width=2,
                                       border_color=ACCENT, width=776, height=130)
        self.drop_frame.place(x=12, y=12)
        lbl = ctk.CTkLabel(self.drop_frame, text="⬆", font=("Segoe UI", 22), text_color=TEXT_MAIN)
        lbl.pack(pady=(10, 0))
        ctk.CTkLabel(self.drop_frame, textvariable=self.source_label_text,
                     font=("Segoe UI", 11), text_color=TEXT_SUB, wraplength=560).pack()
        row = ctk.CTkFrame(self.drop_frame, fg_color="transparent")
        row.pack(pady=6)
        ctk.CTkButton(row, text="Обзор", width=100, fg_color=ACCENT,
                      command=self.on_browse_source).pack(side="left", padx=6)
        ctk.CTkButton(row, text="Папка вывода", width=130, fg_color="#3a3a3a",
                      command=self.on_browse_output).pack(side="left", padx=6)

        if self._dnd_files:
            try:
                self.drop_frame.drop_target_register(self._dnd_files)  # type: ignore[attr-defined]
                self.drop_frame.dnd_bind("<<Drop>>", self._on_drop)  # type: ignore[attr-defined]
            except Exception:
                pass

        # Средняя зона: настройки (слева) + лог (справа)
        self.settings = ctk.CTkFrame(self.root, fg_color=PANEL, width=260, height=300)
        self.settings.place(x=12, y=150)

        ctk.CTkLabel(self.settings, text="НАСТРОЙКИ", font=("Segoe UI", 11, "bold"),
                     text_color=TEXT_MAIN).pack(anchor="w", padx=12, pady=(10, 4))

        ctk.CTkLabel(self.settings, text="Формат вывода", font=("Segoe UI", 10),
                     text_color=TEXT_SUB).pack(anchor="w", padx=12)
        self.fmt_var = tk.StringVar(value="WebP")
        self.fmt_box = ctk.CTkOptionMenu(self.settings, values=["WebP", "JPEG"], variable=self.fmt_var,
                                         command=lambda _: self._persist())
        self.fmt_box.pack(fill="x", padx=12, pady=2)

        ctk.CTkLabel(self.settings, text="Метод интерполяции", font=("Segoe UI", 10),
                     text_color=TEXT_SUB).pack(anchor="w", padx=12)
        self.interp_var = tk.StringVar(value="Lanczos")
        ctk.CTkOptionMenu(self.settings, values=["Lanczos (Рекомендуется)", "Bicubic", "Bilinear"],
                          variable=self.interp_var, command=lambda _: self._persist()).pack(fill="x", padx=12, pady=2)

        qrow = ctk.CTkFrame(self.settings, fg_color="transparent")
        qrow.pack(fill="x", padx=12, pady=(6, 0))
        ctk.CTkLabel(qrow, text="Качество", font=("Segoe UI", 10), text_color=TEXT_SUB).pack(side="left")
        self.q_label = ctk.CTkLabel(qrow, text="80%", font=("Segoe UI", 10, "bold"), text_color=TEXT_MAIN)
        self.q_label.pack(side="right")
        self.quality = ctk.CTkSlider(self.settings, from_=10, to=100, number_of_steps=90,
                                     command=self._on_quality)
        self.quality.pack(fill="x", padx=12, pady=2)

        self.resize_var = tk.BooleanVar(value=False)
        self.keep_var = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(self.settings, text="Изменить разрешение", variable=self.resize_var,
                        command=self._on_resize_toggle).pack(anchor="w", padx=12, pady=(6, 0))
        wh = ctk.CTkFrame(self.settings, fg_color="transparent")
        wh.pack(fill="x", padx=12, pady=2)
        self.w_entry = ctk.CTkEntry(wh, placeholder_text="Ширина", width=90)
        self.w_entry.pack(side="left", padx=(0, 6))
        self.h_entry = ctk.CTkEntry(wh, placeholder_text="Высота", width=90)
        self.h_entry.pack(side="left")
        ctk.CTkCheckBox(self.settings, text="Сохранять пропорции", variable=self.keep_var,
                        command=lambda: self._persist()).pack(anchor="w", padx=12)

        # Лог
        log_frame = ctk.CTkFrame(self.root, fg_color=PANEL, width=506, height=300)
        log_frame.place(x=282, y=150)
        ctk.CTkLabel(log_frame, text="ЖУРНАЛ ВЫВОДА", font=("Segoe UI", 11, "bold"),
                     text_color=TEXT_SUB).pack(anchor="w", padx=12, pady=(10, 4))
        self.log_box = ctk.CTkTextbox(log_frame, font=("Consolas", 10), text_color=TEXT_SUB,
                                      fg_color="#181818", wrap="word")
        self.log_box.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        self.log_box.configure(state="disabled")

        # Нижняя зона: прогресс + кнопки
        ctk.CTkLabel(self.root, textvariable=self._counter_var, font=("Segoe UI", 10),
                     text_color=TEXT_SUB, width=500).place(x=12, y=458)
        self.progress = ctk.CTkProgressBar(self.root, progress_color=ACCENT, width=620, height=14)
        self.progress.place(x=12, y=480)
        self.progress.set(0)

        self.btn_cancel = ctk.CTkButton(self.root, text="Прервать", width=70, height=30, fg_color=ERROR,
                                        hover_color="#8f1d1d", command=self.on_abort)
        self.btn_cancel.place(x=640, y=470)
        self.btn_start = ctk.CTkButton(self.root, text="Начать", width=72, height=30, fg_color=ACCENT,
                                       command=self.on_start)
        self.btn_start.place(x=716, y=470)

        ctk.CTkLabel(self.root, textvariable=self._status_var, font=("Segoe UI", 10),
                     text_color=TEXT_SUB, width=776).place(x=12, y=505)
        self.out_var = tk.StringVar(value="")
        ctk.CTkLabel(self.root, textvariable=self.out_var, font=("Segoe UI", 10),
                     text_color=TEXT_SUB, width=776).place(x=12, y=530)
        self._refresh_out_label()

    # ---------- helpers ----------
    def _interp_value(self) -> str:
        v = self.interp_var.get()
        if v.startswith("Bicubic"):
            return "Bicubic"
        if v.startswith("Bilinear"):
            return "Bilinear"
        return "Lanczos"

    def _apply_config_to_widgets(self):
        d = self.cfg.data
        self.fmt_var.set(d.get("output_format", "WebP"))
        iv = d.get("interpolation", "Lanczos")
        self.interp_var.set({"Lanczos": "Lanczos (Рекомендуется)",
                             "Bicubic": "Bicubic", "Bilinear": "Bilinear"}.get(iv, "Lanczos (Рекомендуется)"))
        try:
            self.quality.set(int(d.get("quality", 80)))
        except Exception:
            pass
        self.q_label.configure(text=f"{int(self.quality.get())}%")
        self.resize_var.set(bool(d.get("resize_enabled", False)))
        self.keep_var.set(bool(d.get("keep_aspect", True)))
        self.w_entry.delete(0, "end"); self.w_entry.insert(0, str(d.get("width", 1920)))
        self.h_entry.delete(0, "end"); self.h_entry.insert(0, str(d.get("height", 1080)))
        self._on_resize_toggle(persist=False)
        self._refresh_out_label()

    def _persist(self):
        try:
            w = int(self.w_entry.get() or 1920)
        except ValueError:
            w = 1920
        try:
            h = int(self.h_entry.get() or 1080)
        except ValueError:
            h = 1080
        self.cfg.update({
            "output_format": self.fmt_var.get(),
            "quality": int(self.quality.get()),
            "interpolation": self._interp_value(),
            "resize_enabled": bool(self.resize_var.get()),
            "width": w, "height": h,
            "keep_aspect": bool(self.keep_var.get()),
            "output_dir": self.cfg.get("output_dir", ""),
        })
        self._refresh_out_label()

    def _refresh_out_label(self):
        od = self.cfg.get("output_dir", "") or "— не выбрана (будет создана ./Converted) —"
        self.out_var.set(f"Выходная папка: {od}")

    def _on_quality(self, v):
        self.q_label.configure(text=f"{int(float(v))}%")

    def _on_resize_toggle(self, persist: bool = True):
        st = "normal" if self.resize_var.get() else "disabled"
        self.w_entry.configure(state=st)
        self.h_entry.configure(state=st)
        if persist:
            self._persist()

    def log(self, msg: str):
        def _do():
            self.log_box.configure(state="normal")
            self.log_box.insert("end", msg + "\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        self.root.after(0, _do)

    def _set_start_enabled(self, enabled: bool):
        self.root.after(0, lambda: self.btn_start.configure(state="normal" if enabled else "disabled"))

    def _set_busy(self, busy: bool):
        def _do():
            s = "disabled" if busy else "normal"
            for w in (self.fmt_box, self.quality, self.w_entry, self.h_entry):
                try:
                    w.configure(state=s)
                except Exception:
                    pass
            if busy:
                self.progress.configure(mode="indeterminate")
                try:
                    self.progress.start()
                except Exception:
                    pass
            else:
                try:
                    self.progress.stop()
                except Exception:
                    pass
                self.progress.configure(mode="determinate")
        self.root.after(0, _do)

    def _flash_drop_error(self):
        def _do():
            try:
                self.drop_frame.configure(border_color=ERROR)
                self.root.after(600, lambda: self.drop_frame.configure(border_color=ACCENT))
            except Exception:
                pass
        self.root.after(0, _do)

    # ---------- источник ----------
    def _on_drop(self, event):
        raw = self.root.tk.splitlist(event.data)
        if raw:
            self.load_source(Path(raw[0].strip("{}")))

    def on_browse_source(self):
        # Сначала пробуем файл-архив, иначе папку
        f = filedialog.askopenfilename(title="Выберите архив",
                                       filetypes=[("Архивы", "*.zip *.rar *.7z"), ("Все", "*.*")])
        if f:
            self.load_source(Path(f))
            return
        d = filedialog.askdirectory(title="Выберите папку с изображениями")
        if d:
            self.load_source(Path(d))

    def on_browse_output(self):
        d = filedialog.askdirectory(title="Папка для результатов")
        if d:
            self.cfg.update({"output_dir": d})
            self._refresh_out_label()
            self.log(f"Папка вывода: {d}")

    def load_source(self, path: Path):
        if self.working:
            return
        self._set_busy(True)
        self._status_var.set("Запуск сквозной валидации…")
        self.log(f"Загружен: {path.name}")
        threading.Thread(target=self._validate_bg, args=(path,), daemon=True).start()

    def _validate_bg(self, path: Path):
        try:
            self.root.after(0, lambda: self._set_start_enabled(False))
            if not path.exists():
                raise ValidationError(f"Путь не найден: {path}")
            if is_archive(path):
                self.log("Запуск сквозной валидации… проверка сигнатур")
                files = validate_and_extract(path, self.cache_dir)
                self.log(f"Валидация успешна. Найдено {len(files)} изображения")
            elif path.is_dir():
                files = collect_from_folder(path)
                if not files:
                    raise ValidationError("В папке нет изображений PNG/JPEG/BMP")
                self.log(f"Найдено {len(files)} изображения в папке")
            elif path.is_file():
                # Одиночный файл
                from archive_validator import SUPPORTED_EXTS, detect_image_kind
                if path.suffix.lower() not in SUPPORTED_EXTS:
                    raise ValidationError(VALIDATION_ERROR_MSG)
                if detect_image_kind(path.read_bytes()[:4]) is None:
                    raise ValidationError(VALIDATION_ERROR_MSG)
                files = [path]
            else:
                raise ValidationError(f"Неподдерживаемый источник: {path}")
            self.source_files = files
            self.root.after(0, lambda: self.source_label_text.set(
                f"{path.name} — {len(files)} файл(ов) готово к конвертации"))
            self._status_var.set("Валидация успешна. Нажмите «Начать».")
            self._set_start_enabled(True)
        except ValidationError as e:
            self.log(f"Ошибка: {e}")
            self._flash_drop_error()
            self.root.after(0, lambda: messagebox.showerror("Ошибка валидации", str(e)))
            self._status_var.set("Ошибка валидации.")
            self.source_files = []
            self._set_start_enabled(False)
        except Exception as e:
            self.log(f"Ошибка: {e}")
            self._set_start_enabled(False)
        finally:
            self._set_busy(False)

    # ---------- старт / стоп ----------
    def _output_dir(self) -> Path:
        od = (self.cfg.get("output_dir", "") or "").strip()
        if od:
            return Path(od)
        # рядом с источником либо ./Converted
        if self.source_files:
            try:
                return self.source_files[0].parent / "Converted"
            except Exception:
                pass
        return Path.cwd() / "Converted"

    def on_start(self):
        if self.working or not self.source_files:
            return
        self._persist()
        self.working = True
        self._set_busy(True)
        self._set_start_enabled(False)
        self.progress.set(0)
        dst = self._output_dir()
        try:
            w = int(self.w_entry.get()) if self.resize_var.get() else None
        except ValueError:
            w = None
        try:
            h = int(self.h_entry.get()) if self.resize_var.get() else None
        except ValueError:
            h = None
        kwargs = {
            "output_format": self.fmt_var.get(),
            "quality": int(self.quality.get()),
            "resize_enabled": bool(self.resize_var.get()),
            "width": w, "height": h,
            "keep_aspect": bool(self.keep_var.get()),
            "interpolation": self._interp_value(),
        }
        self.log(f"Старт: формат={kwargs['output_format']} качество={kwargs['quality']}% "
                 f"интерполяция={kwargs['interpolation']} потоков={self.tm.max_workers}")
        total = len(self.source_files)
        threading.Thread(target=self._convert_bg, args=(list(self.source_files), dst, kwargs, total),
                         daemon=True).start()

    def _convert_bg(self, files, dst, kwargs, total):
        def on_progress(done, tot):
            frac = done / max(1, tot)
            self.root.after(0, lambda d=done, t=tot, f=frac: (
                self.progress.configure(mode="determinate"),
                self.progress.set(f),
                self._counter_var.set(f"Обработано: {d} из {t} ({int(f*100)}%)"),
            ))
        def on_ok(src, out):
            self.log(f"OK  {src.name} -> {out.name}")
        def on_error(src, err):
            self.log(f"ОШИБКА {src.name}: {err}")
        def on_finished(done, tot):
            pass
        try:
            self.tm.start(files, dst, kwargs, on_progress=on_progress,
                          on_ok=on_ok, on_error=on_error, on_finished=on_finished)
            if self.tm.aborted:
                self.log("Прервано пользователем. Очередь очищена.")
                self._status_var.set("Прервано.")
            else:
                self.log(f"Готово. Результаты: {dst}")
                self._status_var.set("Конвертация завершена.")
        finally:
            from config_manager import ConfigManager as CM
            CM.clear_cache(self.cache_dir)
            self.working = False
            self._set_busy(False)
            # Разблокировать для следующего цикла — ТЗ п.5.3
            if self.source_files and not self.tm.aborted:
                self._set_start_enabled(True)

    def on_abort(self):
        if not self.working:
            return
        self.tm.abort()
        self.log("Abort: остановка пула, очистка очереди и кэша…")

    def run(self):
        self.log("PixelForge готов. Источник не выбран.")
        self.root.mainloop()
