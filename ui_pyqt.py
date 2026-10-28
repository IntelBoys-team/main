"""Presentation Layer / UI — PyQt6. Single-Page Dark Mode.

Клиент ТОЛЬКО подключается к уже запущенному серверу (`python server.py`
в Backend-main) через HTTP API: health-check, /api/source, /api/config,
/api/convert, /api/abort и SSE /api/events. Никакой локальной валидации,
распаковки и конвертации в UI нет — алгоритмы живут на сервере (ТЗ п.1.4).
Если API недоступен — статус «Нет связи с API», кнопки заблокированы.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QPropertyAnimation, QPoint
from PyQt6.QtGui import (
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QIntValidator,
    QPainter,
    QPixmap,
    QIcon,
)
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSlider,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from api_client import APIError, PixelForgeClient
from config_manager import ConfigManager

# Адрес API: env PIXELFORGE_API или локальный сервер по умолчанию
API_BASE = os.environ.get("PIXELFORGE_API", "http://127.0.0.1:8000").rstrip("/")
OFFLINE_STATUS = "Нет связи с API — запустите python server.py"


# Палитра — ТЗ п.5.1
BG = "#1E1E1E"
PANEL = "#2D2D2D"
TEXT_MAIN = "#FFFFFF"
TEXT_SUB = "#E0E0E0"
ACCENT = "#007ACC"
ERROR = "#D32F2F"

# Имя файла иконки (лежит рядом со скриптом)
ICON_NAME = "jhjgh-no-bg-preview-_carve.photos_.ico"

HINT_TEXT = (
    "Перетащите сюда папку или архив с изображениями "
    "(.png, .jpg, .bmp, .webp, .gif, .ico, .tiff, .avif, .zip, .rar, .7z) "
    "или нажмите на эту область"
)

INTERP_ITEMS = ["Lanczos (Рекомендуется)", "Bicubic", "Bilinear"]
OUTPUT_FORMATS_UI = ["WebP", "JPEG", "PNG", "BMP", "ICO", "GIF", "TIFF", "AVIF"]

# SVG-иконка загрузки (стрелка вверх)
UPLOAD_SVG_TEMPLATE = """<svg xmlns="http://www.w3.org/2000/svg" width="38" height="38"
viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.5"
stroke-linecap="round" stroke-linejoin="round">
<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
<path d="M17 8l-5-5-5 5"/>
<path d="M12 3v13"/>
</svg>"""


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def _icon_path() -> Path:
    base = Path(getattr(sys, "_MEIPASS", _app_dir()))
    return base / ICON_NAME


def _render_svg_pixmap(svg_text: str, size: int = 38) -> QPixmap:
    renderer = QSvgRenderer(svg_text.encode())
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return pixmap


class DropFrame(QFrame):
    file_dropped = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._style_normal = ""
        self._style_hover = ""
        self._is_dragging = False
        self.drop_icon: QLabel | None = None
        self._icon_anim = None

    def dragEnterEvent(self, event: QDragEnterEvent | None) -> None:
        if event is not None and event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._is_dragging = True
            self.setStyleSheet(self._style_hover)
            self._set_icon_color("#5AA3FF")
            self._move_icon(-2)
        elif event is not None:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._is_dragging = False
        self.setStyleSheet(self._style_normal)
        self._set_icon_color("#6A6A70")
        self._move_icon(2)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent | None) -> None:
        self._is_dragging = False
        self.setStyleSheet(self._style_normal)
        self._set_icon_color("#6A6A70")
        self._move_icon(2)
        if event is None:
            return
        urls = event.mimeData().urls()
        if urls:
            self.file_dropped.emit(urls[0].toLocalFile())
            event.acceptProposedAction()
        else:
            event.ignore()

    def enterEvent(self, event):
        if not self._is_dragging:
            self.setStyleSheet(self._style_hover)
            self._set_icon_color("#5AA3FF")
            self._move_icon(-2)
        super().enterEvent(event)

    def leaveEvent(self, event):
        if not self._is_dragging:
            self.setStyleSheet(self._style_normal)
            self._set_icon_color("#6A6A70")
            self._move_icon(2)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            parent = self.parent()
            while parent and not hasattr(parent, "on_browse_source"):
                parent = parent.parent()
            if parent:
                parent.on_browse_source()
        super().mousePressEvent(event)

    def _set_icon_color(self, color: str):
        if self.drop_icon is None:
            return
        svg = UPLOAD_SVG_TEMPLATE.format(color=color)
        self.drop_icon.setPixmap(_render_svg_pixmap(svg))

    def _move_icon(self, dy: int):
        if self.drop_icon is None:
            return
        start = self.drop_icon.pos()
        end = QPoint(start.x(), start.y() + dy)
        if self._icon_anim is not None:
            self._icon_anim.stop()
        self._icon_anim = QPropertyAnimation(self.drop_icon, b"pos")
        self._icon_anim.setDuration(120)
        self._icon_anim.setStartValue(start)
        self._icon_anim.setEndValue(end)
        self._icon_anim.start()


class ImageConverterWindow(QWidget):
    sig_log = pyqtSignal(str)
    sig_progress = pyqtSignal(int, int)
    sig_status = pyqtSignal(str)
    sig_counter = pyqtSignal(str)
    sig_source = pyqtSignal(str)
    sig_busy = pyqtSignal(bool)
    sig_error = pyqtSignal(str)
    sig_drop_error = pyqtSignal()
    sig_beep = pyqtSignal()
    # События API (эмизируются из фоновых потоков, обрабатываются в GUI)
    sig_api_connected = pyqtSignal(bool)
    sig_job = pyqtSignal(dict)
    sig_source_event = pyqtSignal(dict)
    # Запрос пересчёта кнопок/заморозки: слот всегда читает актуальные флаги,
    # поэтому устаревшие значения из фоновых потоков не перетирают свежие
    sig_refresh = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("PixelForge — Конвертер изображений")
        self.setFixedSize(1113, 700)
        self.setAcceptDrops(True)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet(
            f"background-color: {BG}; color: {TEXT_SUB}; font-family: 'Segoe UI';"
        )

        self._app_icon = QIcon(str(_icon_path())) if _icon_path().exists() else None
        if self._app_icon is not None:
            self.setWindowIcon(self._app_icon)

        self.cfg = ConfigManager()
        self.client = PixelForgeClient(API_BASE, timeout=15.0)
        self.connected: bool | None = None  # None = ещё не проверяли
        self.source_ready = False
        self.working = False
        self._local_busy = False      # HTTP-запрос в полёте (источник/старт)
        self._busy_applied = False    # последнее состояние, отданное в sig_busy
        self._last_job_status = "idle"
        self._server_logs_hydrated = False
        self._drag_pos = None

        self._build()
        self._connect_signals()
        self._apply_config_to_widgets()
        self.btn_start.setEnabled(False)
        self._log("PixelForge готов. Источник не выбран.")
        self._log(f"Подключение к API: {API_BASE} …")
        # Один фоновый поток: health-check + SSE с автопереподключением
        threading.Thread(target=self._connection_loop, daemon=True, name="pf-api").start()

    def _panel_style(self) -> str:
        return f"QFrame {{ background-color: {PANEL}; border-radius: 6px; }}"

    def _build(self):
        # Фон приложения
        self.background = QFrame(self)
        self.background.setGeometry(0, 0, 1113, 700)
        self.background.setStyleSheet(
            f"QFrame {{ background-color: {BG}; border-radius: 10px; }}"
        )
        self.background.lower()

        # Шапка окна
        self.title_bar = QFrame(self)
        self.title_bar.setGeometry(0, 0, 1113, 40)
        self.title_bar.setStyleSheet(
            "background-color: #181818;"
            "border-top-left-radius: 10px;"
            "border-top-right-radius: 10px;"
        )

        title_icon_label = None
        if self._app_icon is not None:
            title_icon_label = QLabel(self.title_bar)
            title_icon_label.setPixmap(self._app_icon.pixmap(28, 28))
            title_icon_label.setGeometry(12, 6, 28, 28)
            title_icon_label.setStyleSheet("background: transparent; border: none;")
        title_text_x = 52 if title_icon_label is not None else 15

        title_label = QLabel("PixelForge — Конвертер изображений", self.title_bar)
        title_label.setStyleSheet("color: white; font-weight: bold; font-size: 10pt; background: transparent;")
        title_label.setGeometry(title_text_x, 10, 400, 20)

        btn_close = QPushButton("✕", self.title_bar)
        btn_close.setGeometry(1070, 5, 35, 30)
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.setStyleSheet(
            "QPushButton { background-color: transparent; color: white; border: none; font-size: 14px; }"
            "QPushButton:hover { background-color: #D32F2F; border-radius: 4px; }"
        )
        btn_close.clicked.connect(self.close)

        btn_min = QPushButton("—", self.title_bar)
        btn_min.setGeometry(1030, 5, 35, 30)
        btn_min.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_min.setStyleSheet(
            "QPushButton { background-color: transparent; color: white; border: none; font-size: 14px; }"
            "QPushButton:hover { background-color: #3a3a3a; border-radius: 4px; }"
        )
        btn_min.clicked.connect(self.showMinimized)

        # Верхняя зона: импорт (Drag-and-Drop)
        self.drop_frame = DropFrame(self)
        self.drop_frame.setGeometry(12, 52, 1090, 156)

        self._drop_style_normal = (
            f"QFrame {{ background-color: {PANEL}; "
            f"border: 2px dashed #3A3A3D; border-radius: 6px; }}"
        )
        self._drop_style_hover = (
            "QFrame { background-color: rgba(59, 130, 246, 25); "
            "border: 2px dashed #3B82F6; border-radius: 6px; }"
        )
        self._drop_style_error = (
            f"QFrame {{ background-color: {PANEL}; "
            f"border: 2px dashed {ERROR}; border-radius: 6px; }}"
        )
        self.drop_frame.setStyleSheet(self._drop_style_normal)
        self.drop_frame._style_normal = self._drop_style_normal
        self.drop_frame._style_hover = self._drop_style_hover

        dl = QVBoxLayout(self.drop_frame)
        dl.setContentsMargins(14, 10, 14, 10)
        dl.addStretch(1)

        self.drop_icon = QLabel()
        self.drop_icon.setPixmap(_render_svg_pixmap(UPLOAD_SVG_TEMPLATE.format(color="#6A6A70")))
        self.drop_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_icon.setStyleSheet("border: none; background: transparent;")
        self.drop_icon.setFixedSize(38, 38)
        dl.addWidget(self.drop_icon, 0, Qt.AlignmentFlag.AlignHCenter)
        self.drop_frame.drop_icon = self.drop_icon

        dl.addSpacing(6)

        self.source_label = QLabel(HINT_TEXT)
        self.source_label.setWordWrap(True)
        self.source_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.source_label.setStyleSheet(
            f"color: {TEXT_SUB}; border: none; background: transparent;"
        )
        dl.addWidget(self.source_label)

        dl.addStretch(1)
        self.drop_frame.file_dropped.connect(self.load_source_str)

        # Панель настроек (слева)
        self.settings = QFrame(self)
        self.settings.setGeometry(12, 216, 260, 350)
        self.settings.setObjectName("settingsPanel")
        self.settings.setStyleSheet(
            f"""
            QFrame#settingsPanel {{
                background-color: {PANEL};
                border-radius: 6px;
            }}
            QFrame#settingsPanel QLabel {{
                background-color: transparent;
                color: {TEXT_SUB};
            }}
            QFrame#settingsPanel QCheckBox {{
                background-color: transparent;
                color: {TEXT_SUB};
            }}
            QFrame#settingsPanel QComboBox,
            QFrame#settingsPanel QLineEdit {{
                background-color: #252526;
                color: {TEXT_SUB};
                border: 1px solid #3A3A3D;
                border-radius: 4px;
                padding: 4px;
            }}
            QFrame#settingsPanel QComboBox:hover,
            QFrame#settingsPanel QLineEdit:hover {{
                border: 1px solid #5AA3FF;
            }}
            QFrame#settingsPanel QComboBox:focus,
            QFrame#settingsPanel QLineEdit:focus {{
                border: 1px solid #3B82F6;
            }}
            """
        )
        sl = QVBoxLayout(self.settings)
        sl.setContentsMargins(12, 10, 12, 10)
        sl.setSpacing(3)
        title = QLabel("НАСТРОЙКИ")
        title.setStyleSheet(f"color: {TEXT_MAIN}; font-size: 14pt; font-weight: bold;")
        sl.addWidget(title)

        sl.addWidget(self._sub("Формат вывода"))
        self.fmt_box = QComboBox()
        self.fmt_box.addItems(OUTPUT_FORMATS_UI)
        self.fmt_box.currentTextChanged.connect(lambda _: self._persist())
        sl.addWidget(self.fmt_box)

        sl.addWidget(self._sub("Метод интерполяции"))
        self.interp_box = QComboBox()
        self.interp_box.addItems(INTERP_ITEMS)
        self.interp_box.currentTextChanged.connect(lambda _: self._persist())
        sl.addWidget(self.interp_box)

        qrow = QHBoxLayout()
        qrow.addWidget(self._sub("Качество"))
        qrow.addStretch(1)
        self.q_label = QLabel("80%")
        self.q_label.setStyleSheet(f"color: {TEXT_MAIN}; font-weight: bold; background: transparent;")
        qrow.addWidget(self.q_label)
        sl.addLayout(qrow)

        self.quality = QSlider(Qt.Orientation.Horizontal)
        self.quality.setRange(10, 100)
        self.quality.setValue(80)
        self.quality.valueChanged.connect(self._on_quality)
        #---------------------- НАЧАЛО ВСТАВКИ (ПОЛЗУНОК-БУБЛИК) ----------------------
        self.quality.setStyleSheet(
            """
            QSlider {
                background: transparent;
            }
            QSlider::groove:horizontal {
                height: 6px;
                background: #4A4A4F;
                border-radius: 3px;
            }
            QSlider::sub-page:horizontal {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                            stop:0 #2D7FF9, stop:1 #5AA3FF);
                border-radius: 3px;
                margin: 0px;
            }
            QSlider::add-page:horizontal {
                background: #4A4A4F;
                border-radius: 3px;
                margin: 0px;
            }
            QSlider::handle:horizontal {
                width: 18px;
                height: 18px;
                margin: -8px 0;
                border-radius: 11px;
                background: #F2F2F4;
                border: 4px solid #3B82F6;
            }
            QSlider::handle:horizontal:hover {
                background: #FFFFFF;
                border: 4px solid #5AA3FF;
            }
            """
        )
        #---------------------- КОНЕЦ ВСТАВКИ ----------------------
        sl.addWidget(self.quality)

        checkbox_style = """
            QCheckBox {
                color: #E0E0E0;
                spacing: 8px;
                background-color: transparent;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border-radius: 4px;
                border: 2px solid #3A3A3D;
                background: #2D2D30;
            }
            QCheckBox::indicator:hover {
                border: 2px solid #5AA3FF;
            }
            QCheckBox::indicator:checked {
                background: #3B82F6;
                border: 2px solid #3B82F6;
            }
            QCheckBox::indicator:checked:hover {
                background: #5AA3FF;
                border: 2px solid #5AA3FF;
            }
        """

        self.resize_check = QCheckBox("Изменить разрешение")
        self.resize_check.setStyleSheet(checkbox_style)
        self.resize_check.toggled.connect(self._on_resize_toggle)
        sl.addWidget(self.resize_check)
        wh = QHBoxLayout()
        self.w_entry = QLineEdit()
        self.w_entry.setPlaceholderText("Ширина")
        self.w_entry.setValidator(QIntValidator(1, 16384))
        self.w_entry.setMaximumWidth(110)
        self.w_entry.textChanged.connect(lambda _: self._persist())
        wh.addWidget(self.w_entry)
        self.h_entry = QLineEdit()
        self.h_entry.setPlaceholderText("Высота")
        self.h_entry.setValidator(QIntValidator(1, 16384))
        self.h_entry.setMaximumWidth(110)
        self.h_entry.textChanged.connect(lambda _: self._persist())
        wh.addWidget(self.h_entry)
        wh.addStretch(1)
        sl.addLayout(wh)
        self.keep_check = QCheckBox("Сохранять пропорции")
        self.keep_check.setStyleSheet(checkbox_style)
        self.keep_check.toggled.connect(lambda _: self._persist())
        sl.addWidget(self.keep_check)
        sl.addStretch(1)

        self.btn_outdir = QPushButton("Папка вывода")
        self.btn_outdir.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_outdir.setStyleSheet(
            "QPushButton { background-color: #3a3a3a; color: white; border: none; "
            "border-radius: 6px; padding: 6px; }"
            "QPushButton:hover { background-color: #3B82F6; }"
            "QPushButton:pressed { background-color: #2D7FF9; }"
        )
        self.btn_outdir.clicked.connect(self.on_browse_output)
        sl.addWidget(self.btn_outdir)

        # Окно логов (справа)
        log_frame = QFrame(self)
        log_frame.setGeometry(282, 216, 820, 350)
        log_frame.setStyleSheet(self._panel_style())
        ll = QVBoxLayout(log_frame)
        ll.setContentsMargins(12, 10, 12, 10)
        log_title = QLabel("ЖУРНАЛ ВЫВОДА")
        log_title.setStyleSheet(f"color: {TEXT_SUB}; font-size: 14pt; font-weight: bold;")
        ll.addWidget(log_title)
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setStyleSheet(
            f"background-color: #181818; color: {TEXT_SUB}; border: none; border-radius: 4px;"
        )
        ll.addWidget(self.log_box)

        # Нижняя зона
        self.progress = QProgressBar(self)
        self.progress.setGeometry(12, 580, 940, 16)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setStyleSheet(
            f"QProgressBar {{ background-color: #2D2D2D; border: none; border-radius: 4px; }}"
            f"QProgressBar::chunk {{ background-color: {ACCENT}; border-radius: 4px; }}"
        )
        self.counter_label = QLabel("Обработано: 0 из 0 файлов (0% объектов)", self)
        self.counter_label.setGeometry(12, 602, 940, 20)

        self.btn_cancel = QPushButton("Прервать", self)
        self.btn_cancel.setGeometry(953, 572, 70, 30)
        self.btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cancel.setStyleSheet(
            f"QPushButton {{ background-color: {ERROR}; color: white; border: none; border-radius: 6px; }}"
        )
        self.btn_cancel.clicked.connect(self.on_abort)
        self.btn_start = QPushButton("Старт", self)
        self.btn_start.setGeometry(1029, 572, 72, 30)
        self.btn_start.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_start.setStyleSheet(
            f"QPushButton {{ background-color: {ACCENT}; color: white; border: none; border-radius: 6px; }}"
            "QPushButton:disabled { background-color: #555; color: #aaa; }"
        )
        self.btn_start.clicked.connect(self.on_start)

        self.status_label = QLabel("Ожидание источника…", self)
        self.status_label.setGeometry(12, 626, 1090, 20)
        self.out_label = QLabel("", self)
        self.out_label.setGeometry(12, 650, 1090, 20)
        self._refresh_out_label()

    def _sub(self, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setStyleSheet(f"color: {TEXT_SUB}; background: transparent;")
        return lab

    def _connect_signals(self):
        self.sig_log.connect(self._on_log)
        self.sig_progress.connect(self._on_progress)
        self.sig_status.connect(self.status_label.setText)
        self.sig_counter.connect(self.counter_label.setText)
        self.sig_source.connect(self.source_label.setText)
        self.sig_busy.connect(self._on_busy)
        self.sig_error.connect(self._on_error)
        self.sig_drop_error.connect(self._flash_drop_error)
        self.sig_beep.connect(QApplication.beep)
        self.sig_api_connected.connect(self._on_api_connected)
        self.sig_job.connect(self._on_job)
        self.sig_source_event.connect(self._on_source_snapshot)
        self.sig_refresh.connect(self._refresh_controls)

    def _interp_value(self) -> str:
        v = self.interp_box.currentText()
        if v.startswith("Bicubic"):
            return "Bicubic"
        if v.startswith("Bilinear"):
            return "Bilinear"
        return "Lanczos"

    def _apply_config_to_widgets(self):
        d = self.cfg.data
        self.fmt_box.setCurrentText(d.get("output_format", "WebP"))
        iv = d.get("interpolation", "Lanczos")
        self.interp_box.setCurrentText(
            {"Lanczos": INTERP_ITEMS[0], "Bicubic": "Bicubic", "Bilinear": "Bilinear"}.get(iv, INTERP_ITEMS[0])
        )
        try:
            self.quality.setValue(int(d.get("quality", 80)))
        except (TypeError, ValueError):
            pass
        self.q_label.setText(f"{self.quality.value()}%")
        self.resize_check.setChecked(bool(d.get("resize_enabled", False)))
        self.keep_check.setChecked(bool(d.get("keep_aspect", True)))
        self.w_entry.setText(str(d.get("width", 1920)))
        self.h_entry.setText(str(d.get("height", 1080)))
        self._on_resize_toggle()
        self._refresh_out_label()

    def _persist(self):
        try:
            w = int(self.w_entry.text() or 1920)
        except ValueError:
            w = 1920
        try:
            h = int(self.h_entry.text() or 1080)
        except ValueError:
            h = 1080
        values = {
            "output_format": self.fmt_box.currentText(),
            "quality": int(self.quality.value()),
            "interpolation": self._interp_value(),
            "resize_enabled": bool(self.resize_check.isChecked()),
            "width": w, "height": h,
            "keep_aspect": bool(self.keep_check.isChecked()),
            "output_dir": self.cfg.get("output_dir", ""),
        }
        # Локально — для восстановления виджетов при следующем запуске
        self.cfg.update(values)
        self._refresh_out_label()
        if not self.connected:
            return
        threading.Thread(target=self._persist_bg, args=(dict(values),), daemon=True).start()

    def _persist_bg(self, values: dict):
        try:
            self.client.update_config(**values)
        except Exception:
            # Сервер может быть недоступен: настройки всё равно придут
            # в POST /api/convert при старте задачи.
            pass

    def _refresh_out_label(self):
        od = self.cfg.get("output_dir", "") or "— не выбрана (рядом с источником / Converted) —"
        self.out_label.setText(f"Выходная папка: {od}")

    def _on_quality(self, v: int):
        self.q_label.setText(f"{v}%")
        self._persist()

    def _on_resize_toggle(self):
        enabled = self.resize_check.isChecked()
        self.w_entry.setEnabled(enabled)
        self.h_entry.setEnabled(enabled)

    def _log(self, msg: str):
        self.sig_log.emit(msg)

    def _on_log(self, msg: str):
        self.log_box.append(msg)

    def _on_progress(self, done: int, total: int):
        frac = done / max(1, total)
        pct = int(frac * 100)
        self.progress.setRange(0, 100)
        self.progress.setValue(pct)
        self.sig_counter.emit(f"Обработано: {done} из {total} файлов ({pct}% объектов)")

    def _on_busy(self, busy: bool):
        for w in (self.fmt_box, self.interp_box, self.quality, self.w_entry,
                  self.h_entry, self.resize_check, self.keep_check,
                  self.btn_outdir):
            w.setEnabled(not busy)
        if busy:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 100)
            self._on_resize_toggle()

    def _on_error(self, msg: str):
        QMessageBox.critical(self, "Ошибка", msg)

    def _flash_drop_error(self):
        self.drop_frame.setStyleSheet(self._drop_style_error)
        QTimer.singleShot(600, lambda: self.drop_frame.setStyleSheet(self._drop_style_normal))

    def dragEnterEvent(self, event: QDragEnterEvent | None) -> None:
        if event is not None and event.mimeData().hasUrls():
            event.acceptProposedAction()
        elif event is not None:
            event.ignore()

    def dropEvent(self, event: QDropEvent | None) -> None:
        if event is None:
            return
        urls = event.mimeData().urls()
        if urls:
            self.load_source_str(urls[0].toLocalFile())
            event.acceptProposedAction()
        else:
            event.ignore()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.title_bar.geometry().contains(event.pos()):
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None

    # ---------- API: соединение и события ----------
    def _connection_loop(self):
        """Фоновый поток: health-check → SSE → автопереподключение."""
        while True:
            try:
                try:
                    self.client.health()
                except Exception:
                    self.sig_api_connected.emit(False)
                    time.sleep(2.0)
                    continue
                self.sig_api_connected.emit(True)
                try:
                    self._pull_snapshots()
                    for event in self.client.iter_events():
                        self._handle_event(event)
                except Exception:
                    pass  # обрыв SSE — идём на переподключение
                self.sig_api_connected.emit(False)
            except RuntimeError:
                return  # Qt-объекты уничтожены (выход из приложения)
            time.sleep(1.5)

    def _pull_snapshots(self):
        """Актуальные снимки после (ре)подключения; журнал — только один раз."""
        try:
            job = self.client.get_job()
            source = self.client.get_source()
        except Exception:
            return
        if not self._server_logs_hydrated:
            self._server_logs_hydrated = True
            for line in job.get("logs") or []:
                self.sig_log.emit(str(line))
        self.sig_source_event.emit(source)
        self.sig_job.emit(job)

    def _handle_event(self, event: dict):
        etype = event.get("type")
        if etype == "log":
            self.sig_log.emit(str(event.get("message", "")))
        elif etype == "state":
            job = event.get("job")
            if isinstance(job, dict):
                self.sig_job.emit(job)
        elif etype == "source":
            source = event.get("source")
            if isinstance(source, dict):
                self.sig_source_event.emit(source)

    def _on_api_connected(self, connected: bool):
        if connected == self.connected:
            return
        self.connected = connected
        if connected:
            self._log(f"Соединение с API установлено: {API_BASE}")
            # Сбрасываем оффлайн-статус; снимки ниже сразу уточнят его
            self.sig_status.emit("Ожидание источника…")
            self._refresh_controls()
            return
        # Связь потеряна: размораживаем UI и ждём автоматического переподключения
        self._local_busy = False
        self.working = False
        self._last_job_status = "idle"
        self.sig_status.emit(OFFLINE_STATUS)
        self.sig_source.emit(HINT_TEXT)
        self._refresh_controls()
        self._log("Нет связи с API. Запустите сервер: python server.py")

    def _on_source_snapshot(self, source: dict):
        self.source_ready = bool(source.get("ready"))
        if self.source_ready:
            self.sig_source.emit(str(source.get("label") or HINT_TEXT))
        elif not self._local_busy:
            self.sig_source.emit(HINT_TEXT)
        self._refresh_controls()

    def _on_job(self, job: dict):
        status = str(job.get("status", "idle"))
        self.working = status in ("validating", "converting")
        # Сначала заморозка/кнопки, затем прогресс — чтобы индикатор
        # «бесконечной анимации» при валидации не перетирался процентами
        self._refresh_controls()

        if status in ("converting", "done", "aborted", "error"):
            self.sig_progress.emit(int(job.get("done") or 0), int(job.get("total") or 0))

        if status != self._last_job_status:
            prev = self._last_job_status
            self._last_job_status = status
            text = {
                "idle": "Ожидание источника…",
                "validating": "Запуск сквозной валидации…",
                "ready": "Валидация успешна. Нажмите «Старт».",
                "converting": "Конвертация…",
                "done": "Конвертация завершена.",
                "aborted": "Прервано.",
                "error": "Ошибка.",
            }.get(status)
            if self.connected and text:
                self.sig_status.emit(text)
            if prev == "converting" and status == "done":
                self.sig_beep.emit()

    def _refresh_controls(self):
        """Только GUI-поток: пересчитывает заморозку и кнопку «Старт».

        Читает актуальные self.working/_local_busy/connected/source_ready,
        поэтому вызов по sig_refresh из фонового потока безопасен и всегда
        даёт финальное состояние (никаких устаревших bool из другого потока).
        """
        busy = self.working or self._local_busy
        if busy != self._busy_applied:
            self._busy_applied = busy
            self.sig_busy.emit(busy)
        self.btn_start.setEnabled(
            bool(self.connected and self.source_ready and not busy)
        )

    def _request_refresh(self):
        """Любой поток: запросить пересчёт в GUI-потоке."""
        self.sig_refresh.emit()

    # ---------- источник ----------
    def load_source_str(self, s: str):
        self.load_source(Path(s))

    def on_browse_source(self):
        f, _ = QFileDialog.getOpenFileName(self, "Выберите архив", "", "Архивы (*.zip *.rar *.7z);;Все (*)")
        if f:
            self.load_source(Path(f))
            return
        d = QFileDialog.getExistingDirectory(self, "Выберите папку с изображениями")
        if d:
            self.load_source(Path(d))

    def on_browse_output(self):
        d = QFileDialog.getExistingDirectory(self, "Папка для результатов")
        if d:
            self.cfg.update({"output_dir": d})
            self._refresh_out_label()
            self._log(f"Папка вывода: {d}")
            if self.connected:
                threading.Thread(
                    target=self._persist_bg, args=({"output_dir": d},), daemon=True
                ).start()

    def load_source(self, path: Path):
        """Указывает путь на сервере: валидация/распаковка идут там же (POST /api/source)."""
        if self.working or self._local_busy:
            return
        if not self.connected:
            self.sig_status.emit(OFFLINE_STATUS)
            self.sig_error.emit(
                f"Нет связи с API ({API_BASE}). Запустите сервер: python server.py"
            )
            return
        self._local_busy = True
        self.sig_status.emit("Запуск сквозной валидации…")
        self._refresh_controls()
        self._log(f"Источник: {path}")
        threading.Thread(target=self._set_source_bg, args=(path,), daemon=True).start()

    def _set_source_bg(self, path: Path):
        try:
            self.client.set_source_path(path)
        except APIError as e:
            if e.status_code == 409:
                self._log(f"Занято: {e.detail}")
                self.sig_status.emit("Конвертация уже выполняется.")
            else:
                self.source_ready = False
                self._log(f"Ошибка: {e.detail}. Процесс остановлен.")
                self.sig_drop_error.emit()
                self.sig_error.emit(e.detail)
                self.sig_status.emit("Ошибка валидации.")
        except Exception as e:
            self.source_ready = False
            self._log(f"Нет связи с API: {e}. Процесс остановлен.")
            self.sig_drop_error.emit()
            self.sig_error.emit(f"Нет связи с API: {e}")
            self.sig_status.emit(OFFLINE_STATUS)
        finally:
            self._local_busy = False
            self._request_refresh()

    # ---------- конвертация ----------
    def on_start(self):
        if self.working or self._local_busy or not self.connected or not self.source_ready:
            return
        self._persist()
        try:
            w = int(self.w_entry.text()) if self.resize_check.isChecked() else None
        except ValueError:
            w = None
        try:
            h = int(self.h_entry.text()) if self.resize_check.isChecked() else None
        except ValueError:
            h = None
        payload = {
            "output_format": self.fmt_box.currentText(),
            "quality": int(self.quality.value()),
            "resize_enabled": bool(self.resize_check.isChecked()),
            "width": w, "height": h,
            "keep_aspect": bool(self.keep_check.isChecked()),
            "interpolation": self._interp_value(),
            "output_dir": (self.cfg.get("output_dir", "") or "").strip() or None,
        }
        self._log(f"Старт: формат={payload['output_format']} качество={payload['quality']}% "
                  f"интерполяция={payload['interpolation']}")
        self._local_busy = True
        self._refresh_controls()
        threading.Thread(target=self._start_bg, args=(payload,), daemon=True).start()

    def _start_bg(self, payload: dict):
        """POST /api/convert: сервер запускает пул потоков в фоне (SSE отдаёт прогресс)."""
        try:
            job = self.client.convert(**payload)
            self.sig_job.emit(job)
        except APIError as e:
            self._log(f"Ошибка запуска: {e.detail}")
            self.sig_error.emit(e.detail)
        except Exception as e:
            self._log(f"Нет связи с API: {e}")
            self.sig_error.emit(f"Нет связи с API: {e}")
        finally:
            self._local_busy = False
            self._request_refresh()

    def on_abort(self):
        if not self.working:
            return
        self._log("Abort: остановка пула, очистка очереди и кэша…")
        threading.Thread(target=self._abort_bg, daemon=True, name="pf-abort").start()

    def _abort_bg(self):
        try:
            self.client.abort()
        except Exception as e:
            self._log(f"Abort не выполнен: {e}")


def run() -> int:
    app = QApplication([])
    app.setFont(QFont("Segoe UI", 10))

    icon_path = _icon_path()
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    win = ImageConverterWindow()
    win.show()
    return app.exec()