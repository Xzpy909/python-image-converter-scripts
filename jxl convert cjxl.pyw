import sys
import os
import subprocess
import configparser
from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, 
                             QLabel, QLineEdit, QPushButton, QGroupBox, 
                             QSlider, QComboBox, QCheckBox, QListWidget, 
                             QProgressBar, QFileDialog, QMessageBox, QAbstractItemView,
                             QDoubleSpinBox, QSpinBox, QInputDialog)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QUrl

# Logic adapted from original ConversionWorker
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
        cjxl = self.settings['cjxl_path']
        exiftool = self.settings['exiftool_path']
        
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
            output_path = base_name + ".jxl"

            # --- Build cjxl Command ---
            # Usage: cjxl.exe INPUT OUTPUT [OPTIONS...]
            cmd = [cjxl, input_path, output_path]

            is_jpeg = input_path.lower().endswith(('.jpg', '.jpeg'))
            want_lossy = self.settings['distance'] > 0
            
            if is_jpeg and want_lossy:
                cmd += ["-j", "0"]

            # 1. Distance (-d)
            cmd += ["-d", str(self.settings['distance'])]

            # 2. Effort (-e)
            cmd += ["-e", str(self.settings['effort'])]

            # 3. Brotli Effort
            cmd += ["--brotli_effort", str(self.settings['brotli_effort'])]

            # 4. Photon Noise ISO
            if self.settings['photon_noise_iso'] > 0:
                cmd += ["--photon_noise_iso", str(self.settings['photon_noise_iso'])]

            # Minimal printing
            cmd += ["--quiet"]

            try:
                # Run cjxl silently
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


class JXLConverterApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("JXL Converter (cjxl) - Drag & Drop Support")
        self.resize(650, 800)
        self.accept_drops = True
        
        self.config_file = "jxl_settings.ini"
        self.config = configparser.ConfigParser()
        self.worker = None

        self.init_ui()
        self.load_config()

    def init_ui(self):
        main_layout = QVBoxLayout()
        self.setLayout(main_layout)

        # --- Section 0: Presets ---
        preset_group = QGroupBox("Presets")
        preset_layout = QHBoxLayout()
        
        self.preset_combo = QComboBox()
        self.preset_combo.addItem("Select a preset...")
        self.preset_combo.currentIndexChanged.connect(self.load_preset_from_combo)
        
        btn_save_preset = QPushButton("Save Current Settings")
        btn_save_preset.clicked.connect(self.save_preset_dialog)

        btn_del_preset = QPushButton("Delete Preset")
        btn_del_preset.clicked.connect(self.delete_preset)

        preset_layout.addWidget(QLabel("Load Preset:"))
        preset_layout.addWidget(self.preset_combo, 1) # Stretch factor 1
        preset_layout.addWidget(btn_save_preset)
        preset_layout.addWidget(btn_del_preset)
        
        preset_group.setLayout(preset_layout)
        main_layout.addWidget(preset_group)

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
        self.dist_spin.setValue(1.0) # Default
        self.dist_spin.setDecimals(2)
        dist_layout.addWidget(self.dist_spin)
        
        self.dist_info = QLabel("(1.0 = Visually Lossless)")
        self.dist_spin.valueChanged.connect(self.update_dist_label)
        dist_layout.addWidget(self.dist_info)
        params_layout.addLayout(dist_layout)

        # 2. Effort (Slider)
        eff_layout = QHBoxLayout()
        eff_layout.addWidget(QLabel("Effort (-e):"))
        
        self.effort_slider = QSlider(Qt.Orientation.Horizontal)
        self.effort_slider.setRange(1, 10)
        self.effort_slider.setValue(7)
        
        self.effort_label = QLabel("7")
        self.effort_slider.valueChanged.connect(lambda v: self.effort_label.setText(str(v)))
        
        eff_layout.addWidget(self.effort_slider)
        eff_layout.addWidget(self.effort_label)
        params_layout.addLayout(eff_layout)

        # 3. Brotli Effort (Slider)
        brotli_layout = QHBoxLayout()
        brotli_layout.addWidget(QLabel("Brotli Effort:"))
        
        self.brotli_slider = QSlider(Qt.Orientation.Horizontal)
        self.brotli_slider.setRange(0, 11)
        self.brotli_slider.setValue(9)
        
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
        self.iso_spin.setValue(0)
        iso_layout.addWidget(self.iso_spin)
        
        params_layout.addLayout(iso_layout)

        params_group.setLayout(params_layout)
        main_layout.addWidget(params_group)

        # --- Section 3: File List ---
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

    # --- Config & Presets Methods ---
    def get_current_settings_dict(self):
        """Returns a dict of the current UI values."""
        return {
            "distance": str(self.dist_spin.value()),
            "effort": str(self.effort_slider.value()),
            "brotli_effort": str(self.brotli_slider.value()),
            "photon_noise_iso": str(self.iso_spin.value())
        }

    def apply_settings_dict(self, settings):
        """Applies a dict of settings to the UI."""
        if not settings:
            return
        
        try:
            if "distance" in settings:
                self.dist_spin.setValue(float(settings["distance"]))
            if "effort" in settings:
                self.effort_slider.setValue(int(settings["effort"]))
            if "brotli_effort" in settings:
                self.brotli_slider.setValue(int(settings["brotli_effort"]))
            if "photon_noise_iso" in settings:
                self.iso_spin.setValue(int(settings["photon_noise_iso"]))
        except ValueError:
            pass # Ignore parsing errors

    def load_config(self):
        """Loads paths, current state, and populates presets."""
        if not os.path.exists(self.config_file):
            return

        self.config.read(self.config_file)
        
        # 1. Load Paths
        if "PATHS" in self.config:
            self.cjxl_input.setText(self.config["PATHS"].get("cjxl", ""))
            self.exiftool_input.setText(self.config["PATHS"].get("exiftool", ""))

        # 2. Load Last Used GUI State (Auto-Restore)
        if "GUI_STATE" in self.config:
            self.apply_settings_dict(self.config["GUI_STATE"])

        # 3. Populate Preset List
        self.refresh_preset_combo()

    def save_config(self):
        """Saves paths and current GUI state to config file."""
        # Ensure sections exist
        if "PATHS" not in self.config:
            self.config["PATHS"] = {}
        if "GUI_STATE" not in self.config:
            self.config["GUI_STATE"] = {}

        # Save Paths
        self.config["PATHS"]["cjxl"] = self.cjxl_input.text()
        self.config["PATHS"]["exiftool"] = self.exiftool_input.text()

        # Save Current State (Auto-Save)
        current_settings = self.get_current_settings_dict()
        for key, val in current_settings.items():
            self.config["GUI_STATE"][key] = val

        with open(self.config_file, "w") as f:
            self.config.write(f)

    def closeEvent(self, event):
        """Override close event to save settings automatically."""
        self.save_config()
        event.accept()

    # --- Preset Logic ---
    def refresh_preset_combo(self):
        """Refreshes the combobox items based on config sections starting with PRESET:"""
        current_text = self.preset_combo.currentText()
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        self.preset_combo.addItem("Select a preset...")
        
        for section in self.config.sections():
            if section.startswith("PRESET:"):
                preset_name = section.split("PRESET:", 1)[1]
                self.preset_combo.addItem(preset_name)
        
        # Restore selection if it still exists
        index = self.preset_combo.findText(current_text)
        if index != -1:
            self.preset_combo.setCurrentIndex(index)
        
        self.preset_combo.blockSignals(False)

    def save_preset_dialog(self):
        text, ok = QInputDialog.getText(self, "Save Preset", "Enter preset name:")
        if ok and text:
            section_name = f"PRESET:{text}"
            if section_name not in self.config:
                self.config[section_name] = {}
            
            # Save current settings to this preset section
            current_settings = self.get_current_settings_dict()
            for key, val in current_settings.items():
                self.config[section_name][key] = val
            
            with open(self.config_file, "w") as f:
                self.config.write(f)
            
            self.refresh_preset_combo()
            # Select the newly created preset
            index = self.preset_combo.findText(text)
            if index != -1:
                self.preset_combo.setCurrentIndex(index)
            
            QMessageBox.information(self, "Preset Saved", f"Preset '{text}' saved successfully.")

    def load_preset_from_combo(self):
        preset_name = self.preset_combo.currentText()
        section_name = f"PRESET:{preset_name}"
        
        if section_name in self.config:
            self.apply_settings_dict(self.config[section_name])

    def delete_preset(self):
        preset_name = self.preset_combo.currentText()
        section_name = f"PRESET:{preset_name}"
        
        if section_name in self.config:
            confirm = QMessageBox.question(self, "Confirm Delete", 
                                           f"Are you sure you want to delete preset '{preset_name}'?",
                                           QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if confirm == QMessageBox.StandardButton.Yes:
                self.config.remove_section(section_name)
                with open(self.config_file, "w") as f:
                    self.config.write(f)
                self.refresh_preset_combo()
                self.preset_combo.setCurrentIndex(0) # Reset to default
        else:
            QMessageBox.warning(self, "Error", "Please select a valid preset to delete.")

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
    window = JXLConverterApp()
    window.show()
    sys.exit(app.exec())