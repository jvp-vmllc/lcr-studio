"""Headless smoke test of the full window against the simulated meter."""
import time

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from lcr_studio.app import MainWindow, load_fonts
from lcr_studio.theme import theme


def test_main_window_streams_demo_readings(tmp_path):
    app = QApplication.instance() or QApplication([])
    load_fonts()
    theme.apply("dark")
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)
    settings.setValue("port", "DEMO")
    win = MainWindow(settings)
    win.show()
    readings = []
    win.worker.reading.connect(readings.append)
    deadline = time.monotonic() + 15
    while len(readings) < 3 and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)
    try:
        assert len(readings) >= 3
        assert win.controls.is_connected
        assert win.measure.pvalue.text() not in ("", "—")
        theme.apply("light")          # theme switch must not raise
        for i in range(win.tabs.count()):
            win.tabs.setCurrentIndex(i)
            app.processEvents()
        x_a = win.measure.pplot.getViewBox().viewRange()[0]
        t_a = time.monotonic()
        while time.monotonic() - t_a < 0.6:          # the strip chart must slide with the clock
            app.processEvents()
            time.sleep(0.01)
        x_b = win.measure.pplot.getViewBox().viewRange()[0]
        assert 0.4 < x_b[1] - x_a[1] < 1.5 and abs((x_b[1] - x_b[0]) - 60) < 1e-6
    finally:
        win.close()
        app.processEvents()
