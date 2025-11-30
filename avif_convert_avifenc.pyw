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
        avifenc = self.settings['avifenc_path']
        
        # Windows specific flag to hide the console window
        startup_info = None
        creation_flags = 0
        if os.name == 'nt':
            creation_flags = subprocess.CREATE_NO_WINDOW

        for i, input_path in enumerate(self.files):
            if not self.is_running:
                break

            filename = os.path.basename(input_path)
            self.status_update.emit(f"Processing {i+1}/{total}: {filename}")
            
            base_name = os.path.splitext(input_path)[0]
            output_path = base_name + ".avif"

            # --- Construct avifenc command ---
            # Syntax: avifenc [options] input output
            cmd = [
                avifenc,
                "-j", "all",                          # Use all cores
                "-q", str(self.settings['quality']),  # Quality 0-100
                "-s", str(self.settings['speed']),    # Speed 0-10
                "-y", self.settings['yuv'],           # YUV Format (420, 444, etc)
            ]

            # Add Tune parameter (-a tune=xxx)
            # libaom specific advanced option passed via -a
            tune_mode = self.settings.get('tune', 'psnr')
            if tune_mode:
                cmd.extend(["-a", f"tune={tune_mode}"])

            # Input and Output
            cmd.append(input_path)
            cmd.append(output_path)

            try:
                subprocess.run(
                    cmd, 
                    check=True, 
                    capture_output=True, 
                    text=True,
                    creationflags=creation_flags
                )
                # Note: avifenc copies metadata (Exif/XMP/ICC) by default, 
                # so no separate exiftool step is needed.

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
        self.setWindowTitle("AVIF Converter (avifenc) - Drag & Drop")
        self.resize(650, 650)
        self.accept_drops = True
        
        self.config_file = "avifenc_settings.ini"
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
        h_layout.addWidget(QLabel("avifenc.exe:"))
        self.avifenc_input = QLineEdit()
        h_layout.addWidget(self.avifenc_input)
        btn_browse_avifenc = QPushButton("Browse")
        btn_browse_avifenc.clicked.connect(lambda: self.browse_file(self.avifenc_input))
        h_layout.addWidget(btn_browse_avifenc)
        paths_layout.addLayout(h_layout)

        paths_group.setLayout(paths_layout)
        main_layout.addWidget(paths_group)

        # --- Parameters ---
        params_group = QGroupBox("Encoding Parameters")
        params_layout = QVBoxLayout()

        # Quality (Previously CRF)
        q_layout = QHBoxLayout()
        q_layout.addWidget(QLabel("Quality (0-100):"))
        self.quality_slider = QSlider(Qt.Orientation.Horizontal)
        self.quality_slider.setRange(0, 100)
        self.quality_slider.setValue(65) # Default reasonable quality
        self.quality_label = QLabel("65")
        self.quality_slider.valueChanged.connect(lambda v: self.quality_label.setText(str(v)))
        q_layout.addWidget(self.quality_slider)
        q_layout.addWidget(self.quality_label)
        params_layout.addLayout(q_layout)

        # Speed, Format, Tune
        opts_layout = QHBoxLayout()
        
        # Speed
        opts_layout.addWidget(QLabel("Speed:"))
        self.speed_combo = QComboBox()
        # avifenc uses 0-10 (0=slowest, 10=fastest, default=6)
        for i in range(11):
            label = str(i)
            if i == 0: label += " (Slowest)"
            if i == 6: label += " (Default)"
            if i == 10: label += " (Fastest)"
            self.speed_combo.addItem(label, i)
        self.speed_combo.setCurrentIndex(6) # Select 6 by default
        opts_layout.addWidget(self.speed_combo)

        # YUV Format
        opts_layout.addWidget(QLabel("YUV:"))
        self.yuv_combo = QComboBox()
        # avifenc options: auto, 444, 422, 420, 400
        self.yuv_combo.addItems(["auto", "420", "422", "444", "400"])
        self.yuv_combo.setCurrentText("auto")
        opts_layout.addWidget(self.yuv_combo)

        # Tune (New Request)
        opts_layout.addWidget(QLabel("Tune:"))
        self.tune_combo = QComboBox()
        self.tune_combo.addItems(["psnr", "ssim", "iq"]) 
        self.tune_combo.setCurrentText("ssim")
        self.tune_combo.setToolTip("Sets -a tune=<metric> for libaom")
        opts_layout.addWidget(self.tune_combo)

        params_layout.addLayout(opts_layout)
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
                self.avifenc_input.setText(self.config["PATHS"].get("avifenc", ""))

    def save_config(self):
        self.config["PATHS"] = {
            "avifenc": self.avifenc_input.text()
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
            "Images (*.jpg *.jpeg *.png *.tif *.tiff *.webp *.heic *.y4m)")
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
                if file_path.lower().endswith(('.jpg', '.jpeg', '.png', '.tif', '.tiff', '.webp', '.heic', '.y4m')):
                    files.append(file_path)
        if files:
            self.add_files_to_list(files)

    def start_conversion(self):
        files = [self.file_list.item(i).text() for i in range(self.file_list.count())]
        if not files:
            QMessageBox.warning(self, "No Files", "Please add images to convert.")
            return

        avifenc_path = self.avifenc_input.text()
        if not os.path.exists(avifenc_path):
            QMessageBox.critical(self, "Error", "avifenc.exe path is invalid.")
            return

        settings = {
            'avifenc_path': avifenc_path,
            'quality': self.quality_slider.value(),
            'speed': self.speed_combo.currentData(), # Gets the integer 0-10
            'yuv': self.yuv_combo.currentText(),
            'tune': self.tune_combo.currentText(),
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