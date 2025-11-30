import sys
import os
import subprocess
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QLabel, QLineEdit, QPushButton, QFileDialog, QFrame, QProgressBar,
    QMessageBox, QGroupBox, QFormLayout, QDoubleSpinBox, QSpinBox,
    QGraphicsView, QGraphicsScene, QGraphicsBlurEffect,
    QTextEdit, QScrollArea
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSettings
from PyQt6.QtGui import QPixmap, QTextOption

import humanize


class ConverterThread(QThread):
    progress = pyqtSignal(int)
    status = pyqtSignal(str)
    current_file = pyqtSignal(str)
    finished = pyqtSignal(int, list)
    log_entry = pyqtSignal(str)

    def __init__(self, files, ffmpeg_path, exiftool_path, distance, effort):
        super().__init__()
        self.files = files
        self.ffmpeg_path = ffmpeg_path
        self.exiftool_path = exiftool_path
        self.distance = distance
        self.effort = effort

    def run(self):
        total = len(self.files)
        startupinfo = subprocess.STARTUPINFO() if os.name == "nt" else None
        if startupinfo:
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        success_count = 0
        errors = []

        for idx, input_file in enumerate(self.files):
            path_str = str(input_file)
            self.current_file.emit(path_str)
            self.status.emit(f"Processing {idx + 1}/{total}: {input_file.name}")

            output_file = input_file.with_suffix(".jxl")
            original_size = input_file.stat().st_size

            try:
                # 1. FFmpeg conversion
                subprocess.run([
                    self.ffmpeg_path, "-y", "-i", path_str,
                    "-c:v", "libjxl",
                    "-distance", str(self.distance),
                    "-effort", str(self.effort),
                    "-strict", "-1",
                    "-map_metadata", "0",
                    "-loglevel", "error",
                    str(output_file)
                ], check=True, startupinfo=startupinfo,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)

                # 2. ExifTool metadata copy
                subprocess.run([
                    self.exiftool_path, "-m",
                    "-TagsFromFile", path_str,
                    "-all:all", "-overwrite_original",
                    str(output_file)
                ], check=True, startupinfo=startupinfo,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)

                # 3. Success → calculate savings and log
                if output_file.exists() and output_file.stat().st_size > 0:
                    jxl_size = output_file.stat().st_size
                    change_percent = ((jxl_size - original_size) / original_size) * 100 if original_size > 0 else 0

                    orig_str = humanize.naturalsize(original_size, binary=True)
                    jxl_str = humanize.naturalsize(jxl_size, binary=True)

                    # Determine color and format based on compression/bloat
                    if change_percent < 0:
                        # Compression: show negative percentage in green
                        color = "#00ff00"  # green
                        percent_str = f"{change_percent:.1f}%"
                    elif change_percent > 0:
                        # Bloat: show positive percentage with + in red
                        color = "#ff4444"  # red
                        percent_str = f"+{change_percent:.1f}%"
                    else:
                        # No change: neutral gray
                        color = "#cccccc"  # gray
                        percent_str = "0.0%"

                    entry = (
                        f"{input_file.name} → {output_file.name}<br>"
                        f"  {orig_str} ⇌ {jxl_str} "
                        f"<span style='color:{color}; font-weight:bold;'>({percent_str})</span>"
                    )
                    self.log_entry.emit(entry)
                    success_count += 1
                else:
                    raise OSError("JXL file was not created or is empty")

            except subprocess.CalledProcessError as e:
                cmd = "FFmpeg" if e.cmd[0] == self.ffmpeg_path else "ExifTool"
                err = e.stderr.decode(errors="ignore").strip() if e.stderr else "Unknown error"
                error_line = f"{input_file.name} → {cmd} failed: {err or 'No output'} ✗"
                self.log_entry.emit(error_line)
                errors.append(f"{input_file.name}\n{cmd} error: {err}")

            except Exception as e:
                error_line = f"{input_file.name} → Unexpected error: {str(e)[:100]} ✗"
                self.log_entry.emit(error_line)
                errors.append(f"{input_file.name}\n{str(e)}")

            # Always update progress (even on failure)
            self.progress.emit(int((idx + 1) / total * 100))

        self.finished.emit(success_count, errors)


class PreviewWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(320)
        self.setStyleSheet("background:#111; border-radius:12px;")

        self.scene = QGraphicsScene(self)
        self.view = QGraphicsView(self.scene)
        self.view.setStyleSheet("background:transparent; border:none;")
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)   # ← Fixed!

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)

        self.blur_item = None
        self.sharp_item = None

    def set_image(self, path: str):
        if not path or not Path(path).exists():
            self.clear()
            return

        pixmap = QPixmap(path)
        if pixmap.isNull():
            self.clear()
            return

        view_rect = self.view.viewport().rect()

        # Sharp foreground
        sharp_size = view_rect.size() * 0.75
        sharp_pixmap = pixmap.scaled(sharp_size, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation)

        # Blurred background – make it large enough to cover the entire panel in any aspect ratio
        max_dim = max(view_rect.width(), view_rect.height())
        blur_target = view_rect.size() * 2.0  # 2.0× is more than enough
        blur_pixmap = pixmap.scaled(blur_target,
                                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,  # ← Key change!
                                    Qt.TransformationMode.SmoothTransformation)
        # Optional: crop to exact viewport size after scaling to avoid huge pixmaps
        blur_pixmap = blur_pixmap.copy(
            (blur_pixmap.width() - view_rect.width()) // 2,
            (blur_pixmap.height() - view_rect.height()) // 2,
            view_rect.width(),
            view_rect.height()
        )

        self.scene.clear()
        self.blur_item = None
        self.sharp_item = None

        # 1. Blurred background
        self.blur_item = self.scene.addPixmap(blur_pixmap)
        blur_effect = QGraphicsBlurEffect()
        blur_effect.setBlurRadius(28)
        blur_effect.setBlurHints(QGraphicsBlurEffect.BlurHint.PerformanceHint)
        self.blur_item.setGraphicsEffect(blur_effect)
        self.blur_item.setZValue(0)

        # 2. Sharp foreground
        self.sharp_item = self.scene.addPixmap(sharp_pixmap)
        self.sharp_item.setZValue(10)

        self.center_items()

    def center_items(self):
        if not self.sharp_item:
            return
        r = self.view.viewport().rect()

        # Center sharp
        br = self.sharp_item.boundingRect()
        self.sharp_item.setPos((r.width() - br.width()) / 2, (r.height() - br.height()) / 2)

        # Center blur
        if self.blur_item:
            brb = self.blur_item.boundingRect()
            self.blur_item.setPos((r.width() - brb.width()) / 2, (r.height() - brb.height()) / 2)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.center_items()

    def clear(self):
        self.scene.clear()
        self.blur_item = None
        self.sharp_item = None


class DropArea(QFrame):
    files_dropped = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setFixedHeight(120)  # Much smaller than before
        self.setStyleSheet("""
            DropArea {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #f0f8ff, stop:1 #e6f3ff);
                border: 2px dashed #3399ff;
                border-radius: 12px;
            }
            DropArea:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #e6f7ff, stop:1 #d6ecff);
                border-color: #0077cc;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 15, 20, 15)
        
        title = QLabel("Drop Images or Folders Here")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 15px; font-weight: bold; color: #2277bb;")
        
        subtitle = QLabel("Supports JPG, PNG, TIFF, WEBP, HEIC…")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet("font-size: 12px; color: #555;")

        layout.addStretch()
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addStretch()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = []
        for url in event.mimeData().urls():
            p = Path(url.toLocalFile())
            if p.is_dir():
                for ext in ("*.jpg","*.jpeg","*.png","*.tif","*.tiff","*.webp","*.heic"):
                    paths.extend(p.rglob(ext.lower()))
                    paths.extend(p.rglob(ext.upper()))
            else:
                paths.append(p)
        seen = set()
        paths = [p for p in paths if not (p in seen or seen.add(p))]
        self.files_dropped.emit(paths)
        event.acceptProposedAction()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("JXL Converter + Live Preview (Final)")
        self.resize(1150, 720)

        self.settings = QSettings("xzpymaps", "JXLConverterPro")
        self.files_to_process = []

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)

        # LEFT – Controls
        left = QWidget()
        left_layout = QVBoxLayout(left)

        # Tools
        tools = QGroupBox("External Tools")
        fl = QFormLayout(tools)
        self.ffmpeg_edit = QLineEdit()
        b1 = QPushButton("Browse…"); b1.clicked.connect(self.browse_ffmpeg)
        h1 = QHBoxLayout(); h1.addWidget(self.ffmpeg_edit); h1.addWidget(b1)
        fl.addRow("FFmpeg:", h1)

        self.exiftool_edit = QLineEdit()
        b2 = QPushButton("Browse…"); b2.clicked.connect(self.browse_exiftool)
        h2 = QHBoxLayout(); h2.addWidget(self.exiftool_edit); h2.addWidget(b2)
        fl.addRow("ExifTool:", h2)
        left_layout.addWidget(tools)

        # Parameters
        param = QGroupBox("JXL Parameters")
        pl = QFormLayout(param)
        self.dist_spin = QDoubleSpinBox(); self.dist_spin.setRange(0.0,15.0); self.dist_spin.setSingleStep(0.1); self.dist_spin.setValue(1.0)
        self.effort_spin = QSpinBox(); self.effort_spin.setRange(1,9); self.effort_spin.setValue(7)
        pl.addRow("Distance:", self.dist_spin)
        pl.addRow("Effort:", self.effort_spin)
        left_layout.addWidget(param)

        # Drop area (now smaller)
        self.drop_area = DropArea()
        self.drop_area.files_dropped.connect(self.add_files)
        left_layout.addWidget(self.drop_area)

        # File counter
        self.counter_label = QLabel("0 files selected")
        self.counter_label.setStyleSheet("font-weight:bold; margin-top:8px;")
        left_layout.addWidget(self.counter_label)

        # === NEW: Conversion Log Panel ===
        log_group = QGroupBox("Conversion Log")
        log_group.setStyleSheet("QGroupBox { font-weight: bold; }")
        log_layout = QVBoxLayout(log_group)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMinimumHeight(180)
        self.log_text.setStyleSheet("""
            QTextEdit {
                background: #1e1e1e;
                color: #00ff00;
                font-family: Consolas, Monaco, monospace;
                font-size: 12px;
                border-radius: 8px;
            }
        """)
        self.log_text.setWordWrapMode(QTextOption.WrapMode.NoWrap)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.log_text)
        log_layout.addWidget(scroll)

        left_layout.addWidget(log_group)

        # Clear, Start buttons
        btn_layout = QHBoxLayout()
        self.clear_btn = QPushButton("Clear List")
        self.clear_btn.clicked.connect(self.clear_files)
        self.start_btn = QPushButton("START CONVERSION")
        self.start_btn.setStyleSheet("background:#4CAF50;color:white;font-weight:bold;padding:10px;font-size:13px;")
        self.start_btn.clicked.connect(self.start_conversion)

        btn_layout.addWidget(self.clear_btn)
        btn_layout.addWidget(self.start_btn)
        left_layout.addLayout(btn_layout)

        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        left_layout.addWidget(self.progress)

        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet("color:#2277bb; font-weight:bold;")
        left_layout.addWidget(self.status_label)

        # Stretch to push everything up
        left_layout.addStretch()

        # RIGHT – Preview
        self.preview = PreviewWidget()

        title = QLabel("Current Image Preview")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("color:white;font-size:18px;padding:12px;")

        preview_box = QVBoxLayout()
        preview_box.addWidget(title)
        preview_box.addWidget(self.preview)

        preview_container = QWidget()
        preview_container.setLayout(preview_box)
        preview_container.setStyleSheet("background:#222;border-radius:12px;")

        splitter.addWidget(left)
        splitter.addWidget(preview_container)
        splitter.setSizes([680, 470])

        self.load_settings()

    def append_log(self, text):
              self.log_text.insertHtml(text + "<br>")  # <br> adds line break
              # Auto-scroll to bottom
              sb = self.log_text.verticalScrollBar()
              sb.setValue(sb.maximum())

    def load_settings(self):
        self.ffmpeg_edit.setText(self.settings.value("ffmpeg_path", ""))
        self.exiftool_edit.setText(self.settings.value("exiftool_path", ""))
        self.dist_spin.setValue(self.settings.value("distance", 1.0, type=float))
        self.effort_spin.setValue(self.settings.value("effort", 7, type=int))

    def save_settings(self):
        self.settings.setValue("ffmpeg_path", self.ffmpeg_edit.text())
        self.settings.setValue("exiftool_path", self.exiftool_edit.text())
        self.settings.setValue("distance", self.dist_spin.value())
        self.settings.setValue("effort", self.effort_spin.value())

    def browse_ffmpeg(self):
        p,_ = QFileDialog.getOpenFileName(self,"Select ffmpeg.exe","","Executables (*.exe)")
        if p: self.ffmpeg_edit.setText(p); self.save_settings()

    def browse_exiftool(self):
        p,_ = QFileDialog.getOpenFileName(self,"Select exiftool.exe","","Executables (*.exe)")
        if p: self.exiftool_edit.setText(p); self.save_settings()

    def add_files(self, paths):
        for p in paths:
            if p not in self.files_to_process:
                self.files_to_process.append(p)
        count = len(self.files_to_process)
        self.counter_label.setText(f"{count} file{'s' if count != 1 else ''} selected")

    def clear_files(self):
        self.files_to_process.clear()
        self.counter_label.setText("0 files selected")
        self.log_text.clear()

    def start_conversion(self):
        if not self.files_to_process:
            QMessageBox.warning(self, "No files", "Please drag & drop images first.")
            return
        if not (os.path.isfile(self.ffmpeg_edit.text().strip()) and 
                os.path.isfile(self.exiftool_edit.text().strip())):
            QMessageBox.critical(self, "Error", "Invalid FFmpeg or ExifTool path.")
            return

        self.save_settings()
        self.start_btn.setEnabled(False)
        self.progress.setValue(0)
        self.status_label.setText("Starting...")
        self.log_text.clear()

        self.thread = ConverterThread(
            self.files_to_process,
            self.ffmpeg_edit.text().strip(),
            self.exiftool_edit.text().strip(),
            self.dist_spin.value(),
            self.effort_spin.value()
        )
        self.thread.current_file.connect(self.preview.set_image)
        self.thread.progress.connect(self.progress.setValue)
        self.thread.status.connect(self.status_label.setText)
        self.thread.log_entry.connect(self.append_log)
        self.thread.finished.connect(self.conversion_finished)

        self.thread.start()

    def conversion_finished(self, success, errors):
        self.start_btn.setEnabled(True)
        self.status_label.setText("Finished!")
        self.progress.setValue(100)
        if errors:
            QMessageBox.warning(self, "Errors",
                f"{success} succeeded, {len(errors)} failed.\n\nFirst error:\n{errors[0]}")
        else:
            QMessageBox.information(self, "Success", f"All {success} images converted!")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())