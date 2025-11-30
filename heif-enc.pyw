import sys
import os
import subprocess
import configparser
from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, 
                             QLabel, QLineEdit, QPushButton, QGroupBox, 
                             QSlider, QComboBox, QCheckBox, QListWidget, 
                             QProgressBar, QFileDialog, QMessageBox, QAbstractItemView)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QUrl

class ConversionWorker(QThread):
    """
    Worker thread to handle the file conversion process without freezing the UI.
    """
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
        heifenc = self.settings['heifenc_path']
        exiftool = self.settings['exiftool_path']
        
        # Windows specific flag to hide the console window completely
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
            output_path = base_name + ".heic" # Forced HEIC extension

            # Build Command
            cmd = [heifenc, input_path]

            if self.settings['lossless']:
                cmd += ["-p", "lossless=true"]
            else:
                cmd += [
                    "-p", f"quality={self.settings['quality']}",
                    "-p", f"preset={self.settings['preset']}",
                    "-p", f"chroma={self.settings['chroma']}"
                ]

            # Output and Verbose (verbose useful for debug, but we suppress window)
            cmd += ["-o", output_path, "--verbose"]

            try:
                # Run heif-enc silently
                subprocess.run(
                    cmd, 
                    check=True, 
                    capture_output=True, 
                    text=True,
                    creationflags=creation_flags
                )

                # Optional: Copy metadata with ExifTool silently
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

            # Update Progress
            progress_percent = int((i + 1) / total * 100)
            self.progress_update.emit(progress_percent)

        self.finished_signal.emit(f"Processed {total} files.")

    def stop(self):
        self.is_running = False


class HEIFConverterApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("HEIF Converter (PyQt6) - Drag & Drop Support")
        self.resize(650, 750)
        self.accept_drops = True
        
        self.config_file = "heif_settings.ini"
        self.config = configparser.ConfigParser()
        self.worker = None

        self.init_ui()
        self.load_config()

    def init_ui(self):
        main_layout = QVBoxLayout()
        self.setLayout(main_layout)

        # --- Section 1: Tool Paths ---
        paths_group = QGroupBox("Tool Paths")
        paths_layout = QVBoxLayout()
        
        # heif-enc path
        h_layout = QHBoxLayout()
        h_layout.addWidget(QLabel("heif-enc.exe:"))
        self.heifenc_input = QLineEdit()
        h_layout.addWidget(self.heifenc_input)
        btn_browse_heif = QPushButton("Browse")
        btn_browse_heif.clicked.connect(lambda: self.browse_file(self.heifenc_input))
        h_layout.addWidget(btn_browse_heif)
        paths_layout.addLayout(h_layout)

        # exiftool path
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

        # --- Section 2: Encoding Parameters ---
        params_group = QGroupBox("Encoding Parameters (x265)")
        params_layout = QVBoxLayout()

        # Quality
        q_layout = QHBoxLayout()
        q_layout.addWidget(QLabel("Quality (0-100):"))
        self.quality_slider = QSlider(Qt.Orientation.Horizontal)
        self.quality_slider.setRange(0, 100)
        self.quality_slider.setValue(85)
        self.quality_label = QLabel("85")
        self.quality_slider.valueChanged.connect(lambda v: self.quality_label.setText(str(v)))
        q_layout.addWidget(self.quality_slider)
        q_layout.addWidget(self.quality_label)
        params_layout.addLayout(q_layout)

        # Preset & Chroma
        pc_layout = QHBoxLayout()
        pc_layout.addWidget(QLabel("Preset:"))
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(["ultrafast", "superfast", "veryfast", "faster", "fast", 
                                    "medium", "slow", "slower", "veryslow", "placebo"])
        self.preset_combo.setCurrentText("slow")
        pc_layout.addWidget(self.preset_combo)

        pc_layout.addWidget(QLabel("Chroma:"))
        self.chroma_combo = QComboBox()
        self.chroma_combo.addItems(["420", "422", "444"])
        pc_layout.addWidget(self.chroma_combo)
        params_layout.addLayout(pc_layout)

        # Lossless
        self.lossless_check = QCheckBox("Lossless (Overrides quality)")
        params_layout.addWidget(self.lossless_check)

        params_group.setLayout(params_layout)
        main_layout.addWidget(params_group)

        # --- Section 3: File List (Drag & Drop) ---
        files_group = QGroupBox("Input Images (Drag & Drop files here)")
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
        
        # Enable Drag and Drop Events on the ListWidget
        self.file_list.dragEnterEvent = self.dragEnterEvent
        self.file_list.dragMoveEvent = self.dragMoveEvent
        self.file_list.dropEvent = self.dropEvent

        files_layout.addWidget(self.file_list)
        files_group.setLayout(files_layout)
        main_layout.addWidget(files_group)

        # --- Section 4: Actions ---
        action_layout = QVBoxLayout()
        
        self.btn_convert = QPushButton("START CONVERSION")
        self.btn_convert.setMinimumHeight(50)
        self.btn_convert.setStyleSheet("background-color: #d32f2f; color: white; font-weight: bold; font-size: 14px;")
        self.btn_convert.clicked.connect(self.start_conversion)
        action_layout.addWidget(self.btn_convert)

        self.progress_bar = QProgressBar()
        action_layout.addWidget(self.progress_bar)

        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet("color: blue;")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        action_layout.addWidget(self.status_label)

        main_layout.addLayout(action_layout)

    # --- Config Methods ---
    def load_config(self):
        if os.path.exists(self.config_file):
            self.config.read(self.config_file)
            if "PATHS" in self.config:
                self.heifenc_input.setText(self.config["PATHS"].get("heifenc", ""))
                self.exiftool_input.setText(self.config["PATHS"].get("exiftool", ""))

    def save_config(self):
        self.config["PATHS"] = {
            "heifenc": self.heifenc_input.text(),
            "exiftool": self.exiftool_input.text()
        }
        with open(self.config_file, "w") as f:
            self.config.write(f)

    # --- File Handling ---
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
            # Store full path in UserRole or just handle text if paths are unique enough
            # Here we store full path as text for simplicity
            if f not in existing_items:
                self.file_list.addItem(f)

    def clear_files(self):
        self.file_list.clear()

    # --- Drag and Drop Events ---
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
                # Basic image extension check
                if file_path.lower().endswith(('.jpg', '.jpeg', '.png', '.tif', '.tiff', '.webp', '.heic')):
                    files.append(file_path)
        
        if files:
            self.add_files_to_list(files)

    # --- Conversion Logic ---
    def start_conversion(self):
        files = [self.file_list.item(i).text() for i in range(self.file_list.count())]
        
        if not files:
            QMessageBox.warning(self, "No Files", "Please add images to convert.")
            return

        heif_path = self.heifenc_input.text()
        if not os.path.exists(heif_path):
            QMessageBox.critical(self, "Error", "heif-enc.exe path is invalid.")
            return

        # Prepare settings
        settings = {
            'heifenc_path': heif_path,
            'exiftool_path': self.exiftool_input.text(),
            'quality': self.quality_slider.value(),
            'preset': self.preset_combo.currentText(),
            'chroma': self.chroma_combo.currentText(),
            'lossless': self.lossless_check.isChecked()
        }

        # UI State
        self.btn_convert.setEnabled(False)
        self.progress_bar.setValue(0)
        self.file_list.setEnabled(False) # Lock list during process

        # Start Thread
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
    window = HEIFConverterApp()
    window.show()
    sys.exit(app.exec())