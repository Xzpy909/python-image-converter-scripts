import sys
import os
import subprocess
import configparser
from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, 
                             QLabel, QLineEdit, QPushButton, QGroupBox, 
                             QSlider, QComboBox, QListWidget, 
                             QProgressBar, QFileDialog, QMessageBox, QAbstractItemView)
from PyQt6.QtCore import Qt, QThread, pyqtSignal

class ConversionWorker(QThread):
    progress_update = pyqtSignal(int)
    status_update = pyqtSignal(str)
    finished_signal = pyqtSignal(str)
    error_signal = pyqtSignal(str)

    def __init__(self, files, settings):
        super().__init__()
        self.files = files
        self.settings = settings
        self.is_running = True

    def run(self):
        total = len(self.files)
        ffmpeg = self.settings['ffmpeg_path']
        exiftool = self.settings['exiftool_path']
        
        startup_info = None
        creation_flags = 0
        if os.name == 'nt':
            creation_flags = subprocess.CREATE_NO_WINDOW

        preset_map = {
            "Placebo (0)": 0, "Very Slow (1)": 1, "Slower (2)": 2, 
            "Slow (3)": 3, "Medium (4)": 4, "Fast (5)": 5, 
            "Faster (6)": 6, "Very Fast (7)": 7, "Realtime (8)": 8
        }
        cpu_used = preset_map.get(self.settings['preset'], 4)

        for i, input_path in enumerate(self.files):
            if not self.is_running:
                break

            filename = os.path.basename(input_path)
            self.status_update.emit(f"Processing {i+1}/{total}: {filename}")
            
            base_name = os.path.splitext(input_path)[0]
            output_path = base_name + ".avif"

            cmd = [
                ffmpeg, "-y",
                "-i", input_path,
                "-c:v", "libaom-av1",
                "-still-picture", "1",
                "-crf", str(self.settings['crf']),
                "-cpu-used", str(cpu_used),
                "-pix_fmt", self.settings['chroma']
            ]
            
            # Row-mt helps slightly with speed on 10-bit/complex encodings
            cmd += ["-row-mt", "1"] 
            cmd.append(output_path)

            try:
                subprocess.run(
                    cmd, 
                    check=True, 
                    capture_output=True, 
                    text=True,
                    creationflags=creation_flags
                )

                if exiftool and os.path.isfile(exiftool):
                    exif_cmd = [
                        exiftool, "-m", "-overwrite_original",
                        "-TagsFromFile", input_path, "-all:all", output_path
                    ]
                    subprocess.run(
                        exif_cmd, 
                        check=False, 
                        capture_output=True, 
                        creationflags=creation_flags
                    )

            except subprocess.CalledProcessError as e:
                err_msg = e.stderr if e.stderr else str(e)
                self.error_signal.emit(f"Failed: {filename}\n{err_msg}")
            except Exception as e:
                self.error_signal.emit(f"System Error on {filename}:\n{str(e)}")

            progress_percent = int(((i + 1) / total) * 100)
            self.progress_update.emit(progress_percent)

        self.finished_signal.emit(f"Processed {total} files.")

    def stop(self):
        self.is_running = False


class AVIFConverterApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AVIF Converter (FFmpeg/libaom) - Drag & Drop")
        self.resize(650, 700)
        self.accept_drops = True
        
        self.config_file = "avif_settings.ini"
        self.config = configparser.ConfigParser()
        self.worker = None

        self.init_ui()
        self.load_config()

    def init_ui(self):
        main_layout = QVBoxLayout()
        self.setLayout(main_layout)

        # --- Tool Paths ---
        paths_group = QGroupBox("Tool Paths")
        paths_layout = QVBoxLayout()
        
        h_layout = QHBoxLayout()
        h_layout.addWidget(QLabel("ffmpeg.exe:"))
        self.ffmpeg_input = QLineEdit()
        h_layout.addWidget(self.ffmpeg_input)
        btn_browse_ffmpeg = QPushButton("Browse")
        btn_browse_ffmpeg.clicked.connect(lambda: self.browse_file(self.ffmpeg_input))
        h_layout.addWidget(btn_browse_ffmpeg)
        paths_layout.addLayout(h_layout)

        e_layout = QHBoxLayout()
        e_layout.addWidget(QLabel("ExifTool:"))
        self.exiftool_input = QLineEdit()
        e_layout.addWidget(self.exiftool_input)
        btn_browse_exif = QPushButton("Browse")
        btn_browse_exif.clicked.connect(lambda: self.browse_file(self.exiftool_input))
        e_layout.addWidget(btn_browse_exif)
        paths_layout.addLayout(e_layout)

        paths_group.setLayout(paths_layout)
        main_layout.addWidget(paths_group)

        # --- Parameters ---
        params_group = QGroupBox("Encoding Parameters (libaom-av1)")
        params_layout = QVBoxLayout()

        # CRF
        q_layout = QHBoxLayout()
        q_layout.addWidget(QLabel("CRF (0-63):"))
        self.crf_slider = QSlider(Qt.Orientation.Horizontal)
        self.crf_slider.setRange(0, 63)
        self.crf_slider.setValue(24)
        self.crf_slider.setInvertedAppearance(False)
        self.crf_label = QLabel("24")
        self.crf_slider.valueChanged.connect(lambda v: self.crf_label.setText(str(v)))
        q_layout.addWidget(self.crf_slider)
        q_layout.addWidget(self.crf_label)
        params_layout.addLayout(q_layout)

        # Preset & Chroma
        pc_layout = QHBoxLayout()
        pc_layout.addWidget(QLabel("Speed:"))
        self.preset_combo = QComboBox()
        self.preset_combo.addItems([
            "Placebo (0)", "Very Slow (1)", "Slower (2)", "Slow (3)", 
            "Medium (4)", "Fast (5)", "Faster (6)", "Very Fast (7)", "Realtime (8)"
        ])
        self.preset_combo.setCurrentText("Medium (4)")
        pc_layout.addWidget(self.preset_combo)

        pc_layout.addWidget(QLabel("Pixel Format:"))
        self.chroma_combo = QComboBox()
        # --- NEW FORMATS ADDED HERE ---
        self.chroma_combo.addItems([
            "yuv420p", "yuv422p", "yuv444p", 
            "yuv420p10le", "yuv444p10le", 
            "gray", "gray10le"
        ])
        self.chroma_combo.setCurrentText("yuv420p")
        pc_layout.addWidget(self.chroma_combo)
        params_layout.addLayout(pc_layout)

        params_group.setLayout(params_layout)
        main_layout.addWidget(params_group)

        # --- File List ---
        files_group = QGroupBox("Input Images")
        files_layout = QVBoxLayout()

        btn_files_layout = QHBoxLayout()
        btn_add = QPushButton("Add Images")
        btn_add.clicked.connect(self.add_files_dialog)
        btn_clear = QPushButton("Clear List")
        btn_clear.clicked.connect(self.clear_files)
        btn_files_layout.addWidget(btn_add)
        btn_files_layout.addWidget(btn_clear)
        btn_files_layout.addStretch()
        files_layout.addLayout(btn_files_layout)

        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.file_list.setAcceptDrops(True)
        self.file_list.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.file_list.dragEnterEvent = self.dragEnterEvent
        self.file_list.dragMoveEvent = self.dragMoveEvent
        self.file_list.dropEvent = self.dropEvent

        files_layout.addWidget(self.file_list)
        files_group.setLayout(files_layout)
        main_layout.addWidget(files_group)

        # --- Actions ---
        action_layout = QVBoxLayout()
        self.btn_convert = QPushButton("START CONVERSION")
        self.btn_convert.setMinimumHeight(50)
        self.btn_convert.setStyleSheet("background-color: #0078D7; color: white; font-weight: bold; font-size: 14px;")
        self.btn_convert.clicked.connect(self.start_conversion)
        action_layout.addWidget(self.btn_convert)

        self.progress_bar = QProgressBar()
        action_layout.addWidget(self.progress_bar)

        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet("color: green;")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        action_layout.addWidget(self.status_label)

        main_layout.addLayout(action_layout)

    def load_config(self):
        if os.path.exists(self.config_file):
            self.config.read(self.config_file)
            if "PATHS" in self.config:
                self.ffmpeg_input.setText(self.config["PATHS"].get("ffmpeg", ""))
                self.exiftool_input.setText(self.config["PATHS"].get("exiftool", ""))

    def save_config(self):
        self.config["PATHS"] = {
            "ffmpeg": self.ffmpeg_input.text(),
            "exiftool": self.exiftool_input.text()
        }
        with open(self.config_file, "w") as f:
            self.config.write(f)

    def browse_file(self, line_edit):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Executable", "", "Executables (*.exe);;All Files (*)")
        if file_path:
            line_edit.setText(file_path)
            self.save_config()

    def add_files_dialog(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select Images", "", 
            "Images (*.jpg *.jpeg *.png *.tif *.tiff *.webp *.heic)")
        self.add_files_to_list(files)

    def add_files_to_list(self, files):
        existing_items = {self.file_list.item(i).text() for i in range(self.file_list.count())}
        for f in files:
            if f not in existing_items:
                self.file_list.addItem(f)

    def clear_files(self):
        self.file_list.clear()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        files = []
        for url in event.mimeData().urls():
            file_path = url.toLocalFile()
            if os.path.isfile(file_path):
                if file_path.lower().endswith(('.jpg', '.jpeg', '.png', '.tif', '.tiff', '.webp', '.heic')):
                    files.append(file_path)
        if files:
            self.add_files_to_list(files)

    def start_conversion(self):
        files = [self.file_list.item(i).text() for i in range(self.file_list.count())]
        if not files:
            QMessageBox.warning(self, "No Files", "Please add images to convert.")
            return

        ffmpeg_path = self.ffmpeg_input.text()
        if not os.path.exists(ffmpeg_path):
            QMessageBox.critical(self, "Error", "ffmpeg.exe path is invalid.")
            return

        settings = {
            'ffmpeg_path': ffmpeg_path,
            'exiftool_path': self.exiftool_input.text(),
            'crf': self.crf_slider.value(),
            'preset': self.preset_combo.currentText(),
            'chroma': self.chroma_combo.currentText(),
        }

        self.btn_convert.setEnabled(False)
        self.progress_bar.setValue(0)
        self.file_list.setEnabled(False)

        self.worker = ConversionWorker(files, settings)
        self.worker.progress_update.connect(self.progress_bar.setValue)
        self.worker.status_update.connect(self.status_label.setText)
        self.worker.error_signal.connect(lambda msg: QMessageBox.warning(self, "Conversion Error", msg))
        self.worker.finished_signal.connect(self.on_conversion_finished)
        self.worker.start()

    def on_conversion_finished(self, msg):
        self.status_label.setText("Done.")
        self.btn_convert.setEnabled(True)
        self.file_list.setEnabled(True)
        QMessageBox.information(self, "Success", msg)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = AVIFConverterApp()
    window.show()
    sys.exit(app.exec())