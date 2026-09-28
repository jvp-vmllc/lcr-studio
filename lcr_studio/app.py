"""LCR Studio main window and entry point."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QFontDatabase, QIcon
from PySide6.QtWidgets import (QAbstractButton, QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QMainWindow, QPushButton, QTabWidget, QVBoxLayout, QWidget)
from PySide6.QtWidgets import QAbstractSpinBox, QComboBox, QPlainTextEdit

from . import __version__
from .panels.console import ConsolePanel
from .panels.controls import ControlPanel
from .panels.flyback import FlybackPanel
from .panels.logger import LogPanel
from .panels.measure import MeasurePanel
from .panels.sweep import SweepPanel
from .panels.tools import ToolsPanel
from .theme import theme
from .ut622e import DEMO_PORT, find_ports
from .widgets import Badge, muted
from .worker import MeterWorker

APP_NAME = "LCR Studio"
ASSETS = Path(__file__).resolve().parent / "assets"


def app_icon() -> QIcon:
    return QIcon(str(ASSETS / "icon.png"))


def load_fonts():
    for ttf in sorted((ASSETS / "fonts").glob("*.ttf")):
        QFontDatabase.addApplicationFont(str(ttf))


class MainWindow(QMainWindow):
    def __init__(self, settings: QSettings | None = None):
        super().__init__()
        self.settings = settings or QSettings("LCRStudio", "LCRStudio")
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.resize(1500, 920)
        self.setMinimumSize(1180, 720)

        self.worker = MeterWorker()
        self.port_name = ""

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._top_bar())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.controls = ControlPanel(self.worker)
        body.addWidget(self.controls)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.measure = MeasurePanel()
        self.flyback = FlybackPanel(self.worker, self.settings)
        self.sweep = SweepPanel(self.worker)
        self.log = LogPanel()
        self.tools = ToolsPanel()
        self.console = ConsolePanel(self.worker)
        for w, name in [(self.measure, "Measure"), (self.flyback, "Flyback"), (self.sweep, "Sweep"),
                        (self.log, "Data log"), (self.tools, "Tools"), (self.console, "Console")]:
            self.tabs.addTab(w, name)
        body.addWidget(self.tabs, 1)
        root.addLayout(body, 1)

        self.status_msg = QLabel("")
        self.statusBar().addWidget(self.status_msg, 1)
        self.statusBar().setSizeGripEnabled(False)

        self._wire()
        self._no_focus_buttons()
        geo = self.settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        self.tabs.setCurrentIndex(int(self.settings.value("tab", 0)))
        self.worker.start()
        QTimer.singleShot(150, self._auto_connect)

    # ------------------------------------------------------------------
    def _top_bar(self):
        bar = QFrame()
        bar.setObjectName("TopBar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 10, 16, 10)
        icon = QLabel()
        icon.setPixmap(app_icon().pixmap(30, 30))
        title = QLabel(APP_NAME)
        title.setObjectName("AppTitle")
        sub = muted(f"UNI-T UT622E LCR meter  ·  v{__version__}")
        sub.setWordWrap(False)
        lay.addWidget(icon)
        lay.addWidget(title)
        lay.addSpacing(6)
        lay.addWidget(sub)
        lay.addStretch(1)
        self.rec_badge = Badge("● REC", "bad")
        self.rec_badge.hide()
        self.conn_badge = Badge("DISCONNECTED")
        lay.addWidget(self.rec_badge)
        lay.addWidget(self.conn_badge)
        self.theme_btn = QPushButton()
        self.theme_btn.setObjectName("Icon")
        self.theme_btn.setToolTip("Switch light / dark theme")
        self.theme_btn.clicked.connect(self._toggle_theme)
        lay.addWidget(self.theme_btn)
        return bar

    def _wire(self):
        w = self.worker
        self.controls.connectRequested.connect(self._connect)
        self.controls.disconnectRequested.connect(w.close_port)
        w.connected.connect(self._on_connected)
        w.disconnected.connect(self._on_disconnected)
        w.settingsChanged.connect(self._on_settings)
        w.reading.connect(self._on_reading)
        w.status.connect(lambda m: self._msg(m, 6000))
        w.progress.connect(self.sweep.on_progress)
        w.progress.connect(self.flyback.on_progress)
        w.jobPartial.connect(self.flyback.on_partial)
        w.jobDone.connect(self.flyback.on_done)
        w.jobError.connect(self.flyback.on_error)
        w.jobPartial.connect(self.sweep.on_partial)
        w.jobDone.connect(self.sweep.on_done)
        w.jobError.connect(self.sweep.on_error)
        w.jobError.connect(self.console.on_error)
        w.jobError.connect(self._on_job_error)
        w.jobDone.connect(self._on_job_done)
        w.traffic.connect(self.console.on_traffic)
        self.measure.snapshot.connect(self.log.add)
        self.log.recordingChanged.connect(self.rec_badge.setVisible)
        theme.changed.connect(lambda c: self.theme_btn.setText("☀" if theme.name == "dark" else "☾"))
        self.theme_btn.setText("☀" if theme.name == "dark" else "☾")

    def _no_focus_buttons(self):
        # Keyboard shortcuts (Space, H, R, S) should never be swallowed by a focused button.
        for b in self.findChildren(QAbstractButton):
            b.setFocusPolicy(Qt.NoFocus)

    def _msg(self, text, ms=4000):
        self.status_msg.setText(text)
        if ms:
            QTimer.singleShot(ms, lambda t=text: self.status_msg.text() == t and self.status_msg.setText(""))

    # ---------------------------------------------------------- connection --
    def _auto_connect(self):
        saved = self.settings.value("port", "")
        baud = int(self.settings.value("baud", 9600))
        ports = find_ports()
        devs = [p[0] for p in ports]
        if saved == DEMO_PORT:
            target = DEMO_PORT
        else:
            target = saved if saved in devs else next((p[0] for p in ports if p[2]), None)
        if target and not self.controls.is_connected:
            self.controls.select(target, baud)
            self._connect(target, baud)

    def _connect(self, port, baud):
        self.port_name = port
        self.conn_badge.setText(f"CONNECTING {port}…")
        self.conn_badge.set_kind("warn")
        self.controls.connect_btn.setEnabled(False)
        self.worker.open_port(port, baud)

    def _on_connected(self, idn):
        self.settings.setValue("port", self.port_name)
        self.settings.setValue("baud", self.controls.baud.currentData())
        self.controls.set_connected(True, idn)
        self.sweep.set_connected(True)
        self.flyback.set_connected(True, idn)
        model = idn.split(",")[1] if "," in idn else "meter"
        self.conn_badge.setText(f"● {model} · {'DEMO' if self.port_name == DEMO_PORT else self.port_name}")
        self.conn_badge.set_kind("good")
        self._msg(f"Connected: {idn}")

    def _on_disconnected(self, reason):
        self.controls.set_connected(False)
        self.sweep.set_connected(False)
        self.flyback.set_connected(False)
        self.conn_badge.setText("DISCONNECTED")
        self.conn_badge.set_kind("bad" if reason else "")
        if reason:
            self._msg(reason, 0)

    def _on_settings(self, st):
        self.controls.apply_settings(st)
        self.measure.apply_settings(st)
        self.sweep.apply_settings(st)
        self.flyback.apply_settings(st)

    def _on_reading(self, r):
        self.measure.add_reading(r)
        self.log.on_reading(r)
        self.tools.on_reading(r)

    def _on_job_error(self, tag, msg):
        if tag not in ("sweep", "flyback"):
            self._msg(f"Command failed: {msg}", 6000)

    def _on_job_done(self, tag, result):
        if tag == "console" and result == "(ok)":      # query replies already appear in the traffic view
            self.console._append(f'<span style="color:{theme.c["text"]}">  {result}</span>')

    # ----------------------------------------------------------------- misc --
    def _toggle_theme(self):
        theme.apply("light" if theme.name == "dark" else "dark")
        self.settings.setValue("theme", theme.name)

    def keyPressEvent(self, ev):
        focus = QApplication.focusWidget()
        if isinstance(focus, (QLineEdit, QAbstractSpinBox, QComboBox, QPlainTextEdit)):
            return super().keyPressEvent(ev)
        k = ev.key()
        if k == Qt.Key_Space and self.worker.state.get("trigger") == "MAN":
            self.worker.trigger()
        elif k == Qt.Key_H:
            self.measure.hold.toggle()
        elif k == Qt.Key_R:
            self.measure.rel.toggle()
        elif k == Qt.Key_S:
            if self.measure.last:
                self.log.add(self.measure.last)
                self._msg("Snapshot added to data log", 2000)
        else:
            return super().keyPressEvent(ev)

    def closeEvent(self, ev):
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("tab", self.tabs.currentIndex())
        self.worker.shutdown()
        super().closeEvent(ev)


def main():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("LCRStudio.UT622E")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setDesktopFileName("lcr-studio")      # matches the .desktop file so Wayland shows the icon
    load_fonts()
    app.setStyle("Fusion")
    app.setWindowIcon(app_icon())
    if "--smoke-test" in sys.argv:
        return smoke_test(app)
    theme.apply(QSettings("LCRStudio", "LCRStudio").value("theme", "dark"))
    win = MainWindow()
    win.show()
    return app.exec()


def smoke_test(app) -> int:
    """Start the full UI against the simulator and exit 0 once readings flow (used by CI on packaged builds)."""
    import tempfile
    theme.apply("dark")
    settings = QSettings(str(Path(tempfile.mkdtemp()) / "smoke.ini"), QSettings.IniFormat)
    settings.setValue("port", DEMO_PORT)
    win = MainWindow(settings)
    win.show()
    result = {"code": 1, "n": 0}

    def on_reading(_r):
        result["n"] += 1
        if result["n"] >= 3:
            result["code"] = 0
            win.close()
            app.quit()
    win.worker.reading.connect(on_reading)
    QTimer.singleShot(20000, lambda: (win.close(), app.quit()))
    app.exec()
    print(f"smoke test: {result['n']} readings, exit {result['code']}", flush=True)
    return result["code"]
