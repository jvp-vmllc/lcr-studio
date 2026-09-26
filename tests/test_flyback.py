import re

import pytest
from PySide6.QtWidgets import QApplication

from lcr_studio.flyback import FlybackProfile, Winding, build_steps, evaluate, run_step
from lcr_studio.report import default_meta, export_pdf, render_image
from lcr_studio.ut622e import SimulatedUT622E


class Ctx:
    aborted = False

    def __init__(self):
        self.partials = []

    def partial(self, d):
        self.partials.append(d)

    def progress(self, i, n, msg):
        pass


@pytest.fixture(scope="module")
def measured():
    meter = SimulatedUT622E()
    meter.set_speed("FAST")
    meter.set_frequency("1kHz")
    meter.set_primary("C")
    p = FlybackProfile(part_number="T1", primary=Winding("Primary", "1-3", 0.5),
                       secondaries=[Winding("12V", "7-9", 0.02), Winding("Aux", "4-5", 0.1)],
                       lp_nom=620e-6, lp_tol=10, llk_max=15e-6, llk_pct_max=2.5, settle=0, navg=1,
                       freqs=["1kHz", "10kHz"], levels=["0.1V", "1.0V"])
    results = {}
    for step in build_steps(p):
        results[step.key] = run_step(meter, Ctx(), step, p.settle, p.navg)
    return meter, p, results


def test_steps_cover_primary_leakage_and_each_winding(measured):
    _, p, _ = measured
    keys = [s.key for s in build_steps(p)]
    assert keys == ["lp", "llk", "sec0", "sec1"]
    llk = build_steps(p)[1]
    assert llk.shorted == [0, 1] and llk.measured == -1


def test_measurements_and_meter_restored(measured):
    meter, p, results = measured
    lp = results["lp"]
    assert len(lp["rows"]) == 4 and lp["dcr"] == pytest.approx(0.42, rel=0.01)
    assert all(600e-6 < r["L"] < 660e-6 for r in lp["rows"])
    assert all(9e-6 < r["L"] < 9.5e-6 for r in results["llk"]["rows"])
    assert len(results["sec0"]["rows"]) == 2           # secondaries: test frequency x levels
    st = meter.read_settings(full=False)
    assert (st["primary"], st["frequency"]) == ("C", "1kHz")


def test_evaluation_passes_and_fails(measured):
    _, p, results = measured
    rows, verdict = evaluate(p, results)
    assert verdict == "FAIL"                           # Aux DCR 0.36 Ω > 0.1 Ω limit
    status = {r["param"]: r["status"] for r in rows}
    assert status["Primary inductance Lp"] == "PASS"
    assert status["Aux DCR"] == "FAIL"
    k = next(r for r in rows if r["param"] == "Coupling coefficient k")
    assert 0.99 < float(k["value"]) < 1.0
    ratio = next(r for r in rows if r["param"].startswith("Turns ratio Primary:12V"))
    assert float(ratio["value"].split(":")[0]) == pytest.approx(6.2, rel=0.005)


def test_incomplete_when_steps_missing(measured):
    _, p, results = measured
    _, verdict = evaluate(p, {"lp": results["lp"]})
    assert verdict == "INCOMPLETE"


def test_pdf_is_one_letter_page(measured, tmp_path):
    _app = QApplication.instance() or QApplication([])
    _, p, results = measured
    out = tmp_path / "r.pdf"
    export_pdf(str(out), p, results, default_meta(serial="S1", meter="UT622E"))
    data = out.read_bytes()
    assert data.startswith(b"%PDF")
    assert len(re.findall(rb"/Type\s*/Page[^s]", data)) == 1
    assert re.search(rb"/MediaBox\s*\[\s*0\s+0\s+612(\.0+)?\s+792(\.0+)?\s*\]", data)
    img = render_image(p, results, default_meta(), dpi=50)
    assert (img.width(), img.height()) == (425, 550)
