"""Headless smoke test of the full window against the simulated meter."""
import time

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from lcr_studio.app import MainWindow, load_fonts
from lcr_studio.flyback import FlybackProfile
from lcr_studio.widgets import BusyDialog
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
        assert win.connection.is_connected
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
        fb = win.flyback                             # a flyback step shows the busy pop-up, then closes it
        fb.set_profile(FlybackProfile(part_number="T", settle=0, navg=1, freqs=["1kHz"], levels=["0.3V"],
                                      lp_freq="1kHz", lp_level="0.3V", llk_freq="1kHz", llk_level="0.3V"))
        assert fb.run_btn.isEnabled() and fb.lp_nom.property("invalid")   # empty limits are outlined in red
        fb.run_ticked()                                                    # Run refuses and warns
        assert fb.busy is None and fb.running_key is None
        assert fb.spec_hint.text().startswith("⚠")
        fb.set_profile(FlybackProfile(part_number="T", settle=0, navg=1, freqs=["1kHz"], levels=["0.3V"],
                                      lp_freq="1kHz", lp_level="0.3V", llk_freq="1kHz", llk_level="0.3V",
                                      lp_nom=620e-6, llk_max=15e-6,
                                      llk_pct_max=2.5))
        assert fb.run_btn.isEnabled() and not fb.lp_nom.property("invalid")
        fb.run_step("lp")
        assert fb.busy is not None and fb.busy.isVisible()
        deadline = time.monotonic() + 30
        while fb.running_key is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.02)
        assert fb.running_key is None and fb.status.get("lp") == "done"
        busy_open = lambda: any(isinstance(d, BusyDialog) and d.isVisible() for d in QApplication.topLevelWidgets())
        deadline = time.monotonic() + 3
        while busy_open() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.02)
        assert not busy_open()
        fb.new_unit()                                # both ticked: Lp runs, then a prompt to wire up for Llk
        fb.run_ticked()
        deadline = time.monotonic() + 30
        while fb.running_key is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.02)
        assert fb.busy is not None and fb.busy.continue_btn.isVisible() and fb.queue == []
        fb.busy.continue_btn.click()
        assert fb.running_key == "llk"
        deadline = time.monotonic() + 30
        while fb.running_key is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.02)
        assert fb.status == {"lp": "done", "llk": "done"}
    finally:
        win.close()
        app.processEvents()
