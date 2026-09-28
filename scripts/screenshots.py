"""Regenerate the README screenshots in docs/images using the simulated meter.

    uv run python scripts/screenshots.py
"""
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lcr_studio.app import MainWindow, app_icon, load_fonts  # noqa: E402
from lcr_studio.flyback import FlybackProfile  # noqa: E402
from lcr_studio.report import render_image  # noqa: E402
from lcr_studio.theme import theme  # noqa: E402

OUT = ROOT / "docs" / "images"
OUT.mkdir(parents=True, exist_ok=True)
app = QApplication(sys.argv)
app.setStyle("Fusion")
app.setWindowIcon(app_icon())
load_fonts()
theme.apply("dark")
settings = QSettings(str(Path(tempfile.mkdtemp()) / "shots.ini"), QSettings.IniFormat)
settings.setValue("port", "DEMO")
w = MainWindow(settings)
w.resize(1480, 900)
w.show()


def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def wait_until(cond, timeout=120):
    end = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > end:
            raise TimeoutError
        pump(0.05)


def shot(name, tab=None):
    if tab is not None:
        w.tabs.setCurrentWidget(tab)
    pump(0.6)
    w.grab().save(str(OUT / f"{name}.png"))
    print("saved", name, flush=True)


def meter(fn):
    w.worker.call(fn, tag="set")
    pump(0.5)


wait_until(lambda: w.connection.is_connected)
meter(lambda m: m.set_speed("FAST"))

# Measure — dark and light
w.tabs.setCurrentWidget(w.measure)
pump(12)
shot("measure", w.measure)
theme.apply("light")
shot("measure-light", w.measure)
theme.apply("dark")

# Flyback — Lp and Llk on the simulated transformer
fb = w.flyback
fb.set_profile(FlybackProfile(part_number="FBT-EE25-12V",
                              lp_nom=620e-6, lp_tol=10, llk_max=15e-6, llk_pct_max=2.5))
w.tabs.setCurrentWidget(fb)
for i in range(len(fb.steps)):
    fb.step_pick.setCurrentIndex(i)
    fb.run_selected()
    wait_until(lambda: fb.running_key is None, 180)
fb.step_pick.setCurrentIndex(1)
shot("flyback", fb)
render_image(fb.profile(), fb.results, fb._meta(), dpi=110).save(str(OUT / "flyback-report.png"))
print("saved flyback-report", flush=True)
meter(lambda m: m.set_fixture(None))          # back to the demo capacitor

# Sweep — series vs parallel model, D and ESR captured
w.tabs.setCurrentWidget(w.sweep)
for lv, cb in w.sweep.level_boxes.items():
    cb.setChecked(lv == "0.3V")
for s, cb in w.sweep.sec_boxes.items():
    cb.setChecked(s in ("D", "ESR"))
for name, equ in (("4.7 µF · series", "SER"), ("4.7 µF · parallel", "PAR")):
    meter(lambda m, e=equ: m.set_equivalent(e))
    w.sweep.name.setText(name)
    w.sweep.start()
    wait_until(lambda: w.sweep.running is None)
w.sweep.sec_pick.setCurrentIndex(w.sweep.sec_pick.findData("ESR"))
meter(lambda m: m.set_equivalent("SER"))
shot("sweep", w.sweep)

# Data log — the simulator inserts a random ±1.2 % part every 3 s
meter(lambda m: m.raw("DEMO:PARTS ON"))
w.tabs.setCurrentWidget(w.log)
w.log.toggle()
pump(45)
w.log.toggle()
shot("data-log", w.log)
meter(lambda m: m.raw("DEMO:PARTS OFF"))

# Console
w.tabs.setCurrentWidget(w.console)
for cmd in ("*IDN?", "FREQ?", "FUNC:IMPA?", "FETC?", "COMP:TOL?"):
    w.console.cmd.setText(cmd)
    w.console.send()
    pump(0.4)
shot("console", w.console)

w.close()
pump(1)
