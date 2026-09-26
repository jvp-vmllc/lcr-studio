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
from lcr_studio.flyback import FlybackProfile, Winding  # noqa: E402
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


wait_until(lambda: w.controls.is_connected)
meter(lambda m: m.set_speed("FAST"))

# Measure — dark and light
w.tabs.setCurrentWidget(w.measure)
pump(12)
shot("measure", w.measure)
theme.apply("light")
shot("measure-light", w.measure)
theme.apply("dark")

# Flyback — full guided test on the simulated transformer
fb = w.flyback
fb.set_profile(FlybackProfile(
    part_number="FBT-EE25-12V", description="65 W flyback, 12 V / 5.4 A",
    primary=Winding("Primary", "1-3", 0.5),
    secondaries=[Winding("12 V out", "7-9", 0.02), Winding("Aux", "4-5", 0.5)],
    lp_nom=620e-6, lp_tol=10, llk_max=15e-6, llk_pct_max=2.5))
fb.serial.setText("SN-000123")
fb.operator.setText("QA bench 2")
w.tabs.setCurrentWidget(fb)
for i in range(len(fb.steps)):
    fb.step_table.setCurrentCell(i, 0)
    fb.run_selected()
    wait_until(lambda: fb.running_key is None, 180)
fb.step_table.setCurrentCell(1, 0)
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

# Sorting + matching — the simulator inserts a random ±1.2 % part every 3 s
meter(lambda m: m.raw("DEMO:PARTS ON"))
w.sorting.nom.setText("4.7u")
w.sorting.bins_edit.setText("0.5, 1, 2, 5")
w.sorting._reset_counts()
w.matching.nom.setText("4.7u")
w.matching.auto.setChecked(True)
w.tabs.setCurrentWidget(w.sorting)
w.log.toggle()
pump(45)
w.matching.auto.setChecked(False)
wait_until(lambda: w.sorting.det.counted)
pump(0.3)
w.log.toggle()
shot("sorting", w.sorting)
w.matching.spread.setValue(0.3)
w.matching.match()
shot("matching", w.matching)
shot("data-log", w.log)
meter(lambda m: m.raw("DEMO:PARTS OFF"))

# Tools
t = w.tools
for edit, text in [(t.an_val, "100n"), (t.an_lossv, "0.012"), (t.rs_l, "10u"), (t.rs_c, "100n"), (t.rs_f, "100k"),
                   (t.tc_r, "10k"), (t.tc_c, "100n"), (t.tc_l, "1m"), (t.cp_c, "470u"), (t.cp_v, "25"),
                   (t.cp_esr, "50m"), (t.cp_i, "1.2"), (t.sv_val, "4.62k"), (t.sv_nom, "4.7k"),
                   (t.nw_vals, "10k, 4.7k, 2.2k"), (t.dq_val, "0.02")]:
    edit.setText(text)
shot("tools", w.tools)

# Console
w.tabs.setCurrentWidget(w.console)
for cmd in ("*IDN?", "FREQ?", "FUNC:IMPA?", "FETC?", "COMP:TOL?"):
    w.console.cmd.setText(cmd)
    w.console.send()
    pump(0.4)
shot("console", w.console)

w.close()
pump(1)
