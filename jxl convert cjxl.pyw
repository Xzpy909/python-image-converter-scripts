import sys
import os
import subprocess
import configparser
from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, 
                             QLabel, QLineEdit, QPushButton, QGroupBox, 
                             QSlider, QComboBox, QCheckBox, QListWidget, 
                             QProgressBar, QFileDialog, QMessageBox, QAbstractItemView,
                             QDoubleSpinBox, QSpinBox) # Added SpinBoxes for JXL params
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QUrl

# Logic adapted from original ConversionWorker [cite: 2]
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
        cjxl = self.settings['cjxl_path'] # Renamed from heifenc_path
        exiftool = self.settings['exiftool_path']
        
        # Windows specific flag to hide the console window [cite: 3, 4]
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
            output_path = base_name + ".jxl" # Changed to .jxl extension [cite: 5]

            # --- Build cjxl Command ---
            # Usage: cjxl.exe INPUT OUTPUT [OPTIONS...]
            cmd = [cjxl, input_path, output_path]

            # 1. Distance (-d)
            # 0.0 = mathematically lossless, 1.0 = visually lossless
            cmd += ["-d", str(self.settings['distance'])]

            # 2. Effort (-e)
            # Range: 1 .. 10
            cmd += ["-e", str(self.settings['effort'])]

            # 3. Brotli Effort
            # Range: 0 .. 11
            cmd += ["--brotli_effort", str(self.settings['brotli_effort'])]

            # 4. Photon Noise ISO
            # Only add if greater than 0
            if self.settings['photon_noise_iso'] > 0:
                cmd += ["--photon_noise_iso", str(self.settings['photon_noise_iso'])]

            # Minimal printing
            cmd += ["--quiet"]

            try:
                # Run cjxl silently [cite: 7, 8]
                subprocess.run(
                    cmd, 
                    check=True, 
                    capture_output=True, 
                    text=True,
                    creationflags=creation_flags
                )

                # Optional: Copy metadata with ExifTool silently [cite: 9]
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

            # Update Progress [cite: 13]
            progress_percent = int((i + 1) / total * 100)
            self.progress_update.emit(progress_percent)

        self.finished_signal.emit(f"Processed {total} files.")

    def stop(self):
        self.is_running = False


class JXLConverterApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("JXL Converter (cjxl) - Drag & Drop Support")
        self.resize(650, 750)
        self.accept_drops = True
        
        self.config_file = "jxl_settings.ini" # Updated config name
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
        
        # cjxl path
        h_layout = QHBoxLayout()
        h_layout.addWidget(QLabel("cjxl.exe:"))
        self.cjxl_input = QLineEdit()
        h_layout.addWidget(self.cjxl_input)
        btn_browse_jxl = QPushButton("Browse")
        btn_browse_jxl.clicked.connect(lambda: self.browse_file(self.cjxl_input))
        h_layout.addWidget(btn_browse_jxl)
        paths_layout.addLayout(h_layout)

        # exiftool path [cite: 16]
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

        # --- Section 2: JXL Parameters ---
        params_group = QGroupBox("JXL Parameters")
        params_layout = QVBoxLayout()

        # 1. Distance (DoubleSpinBox)
        dist_layout = QHBoxLayout()
        dist_label = QLabel("Distance (-d):")
        dist_label.setToolTip("0.0 = Mathematically Lossless.\n1.0 = Visually Lossless.\nRec: 0.5 - 3.0.")
        dist_layout.addWidget(dist_label)
        
        self.dist_spin = QDoubleSpinBox()
        self.dist_spin.setRange(0.0, 25.0)
        self.dist_spin.setSingleStep(0.1)
        self.dist_spin.setValue(1.0) # Default for lossy
        self.dist_spin.setDecimals(2)
        dist_layout.addWidget(self.dist_spin)
        
        # Helper text for distance
        self.dist_info = QLabel("(1.0 = Visually Lossless)")
        self.dist_spin.valueChanged.connect(self.update_dist_label)
        dist_layout.addWidget(self.dist_info)
        params_layout.addLayout(dist_layout)

        # 2. Effort (Slider)
        eff_layout = QHBoxLayout()
        eff_layout.addWidget(QLabel("Effort (-e):"))
        
        self.effort_slider = QSlider(Qt.Orientation.Horizontal)
        self.effort_slider.setRange(1, 10) # Range 1-10
        self.effort_slider.setValue(7)     # Default 7
        
        self.effort_label = QLabel("7")
        self.effort_slider.valueChanged.connect(lambda v: self.effort_label.setText(str(v)))
        
        eff_layout.addWidget(self.effort_slider)
        eff_layout.addWidget(self.effort_label)
        params_layout.addLayout(eff_layout)

        # 3. Brotli Effort (Slider)
        brotli_layout = QHBoxLayout()
        brotli_layout.addWidget(QLabel("Brotli Effort:"))
        
        self.brotli_slider = QSlider(Qt.Orientation.Horizontal)
        self.brotli_slider.setRange(0, 11) # Range 0-11
        self.brotli_slider.setValue(9)     # Default 9
        
        self.brotli_label = QLabel("9")
        self.brotli_slider.valueChanged.connect(lambda v: self.brotli_label.setText(str(v)))
        
        brotli_layout.addWidget(self.brotli_slider)
        brotli_layout.addWidget(self.brotli_label)
        params_layout.addLayout(brotli_layout)

        # 4. Photon Noise ISO (SpinBox)
        iso_layout = QHBoxLayout()
        iso_label = QLabel("Photon Noise ISO:")
        iso_label.setToolTip("Add grain. 0 = None. Higher = More grain (e.g., 3200).")
        iso_layout.addWidget(iso_label)
        
        self.iso_spin = QSpinBox()
        self.iso_spin.setRange(0, 51200)
        self.iso_spin.setSingleStep(100)
        self.iso_spin.setValue(0) # Default 0
        iso_layout.addWidget(self.iso_spin)
        
        params_layout.addLayout(iso_layout)

        params_group.setLayout(params_layout)
        main_layout.addWidget(params_group)

        # --- Section 3: File List (Drag & Drop) [cite: 21, 22] ---
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
        
        self.btn_convert = QPushButton("START JXL CONVERSION")
        self.btn_convert.setMinimumHeight(50)
        # Changed color to specific JXL blue/teal style preference or keep generic
        self.btn_convert.setStyleSheet("background-color: #00796b; color: white; font-weight: bold; font-size: 14px;")
        self.btn_convert.clicked.connect(self.start_conversion)
        action_layout.addWidget(self.btn_convert)

        self.progress_bar = QProgressBar()
        action_layout.addWidget(self.progress_bar)

        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet("color: blue;")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        action_layout.addWidget(self.status_label)

        main_layout.addLayout(action_layout)

    def update_dist_label(self, val):
        if val == 0.0:
            self.dist_info.setText("(Mathematically Lossless)")
        elif val == 1.0:
            self.dist_info.setText("(Visually Lossless)")
        else:
            self.dist_info.setText("")

    # --- Config Methods ---
    def load_config(self):
        if os.path.exists(self.config_file):
            self.config.read(self.config_file)
            if "PATHS" in self.config:
                # Load cjxl path instead of heifenc
                self.cjxl_input.setText(self.config["PATHS"].get("cjxl", ""))
                self.exiftool_input.setText(self.config["PATHS"].get("exiftool", ""))

    def save_config(self):
        self.config["PATHS"] = {
            "cjxl": self.cjxl_input.text(),
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
            "Images (*.jpg *.jpeg *.png *.tif *.tiff *.webp *.gif *.heic)")
        self.add_files_to_list(files)

    def add_files_to_list(self, files):
        existing_items = {self.file_list.item(i).text() for i in range(self.file_list.count())}
        for f in files:
            if f not in existing_items:
                self.file_list.addItem(f)

    def clear_files(self):
        self.file_list.clear()

    # --- Drag and Drop Events [cite: 29, 30] ---
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
                # Extended for JXL input support
                if file_path.lower().endswith(('.jpg', '.jpeg', '.png', '.tif', '.tiff', '.webp', '.heic', '.gif', '.ppm', '.apng')):
                    files.append(file_path)
        
        if files:
            self.add_files_to_list(files)

    # --- Conversion Logic ---
    def start_conversion(self):
        files = [self.file_list.item(i).text() for i in range(self.file_list.count())]
        
        if not files:
            QMessageBox.warning(self, "No Files", "Please add images to convert.")
            return

        cjxl_path = self.cjxl_input.text()
        if not os.path.exists(cjxl_path):
            QMessageBox.critical(self, "Error", "cjxl.exe path is invalid.")
            return

        # Prepare settings
        settings = {
            'cjxl_path': cjxl_path,
            'exiftool_path': self.exiftool_input.text(),
            'distance': self.dist_spin.value(),
            'effort': self.effort_slider.value(),
            'brotli_effort': self.brotli_slider.value(),
            'photon_noise_iso': self.iso_spin.value()
        }

        # UI State
        self.btn_convert.setEnabled(False)
        self.progress_bar.setValue(0)
        self.file_list.setEnabled(False)

        # Start Thread [cite: 34]
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
    window = JXLConverterApp()
    window.show()
    sys.exit(app.exec())