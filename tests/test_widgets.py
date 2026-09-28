import numpy as np

from PySide6.QtWidgets import QApplication

from lcr_studio.widgets import UnitEdit, range_with_limits, smooth_curve


def test_smooth_curve_passes_through_points_and_stays_monotone():
    x = [100, 120, 1000, 10000, 100000]
    y = [620e-6, 621e-6, 622e-6, 627e-6, 640e-6]
    xs, ys = smooth_curve(x, y, log_x=True)
    assert len(xs) >= 160 and xs[0] == 100 and xs[-1] == 100000
    assert ys[0] == y[0] and ys[-1] == y[-1]
    assert min(y) <= ys.min() and ys.max() <= max(y)        # no overshoot between points
    assert np.all(np.diff(ys) >= 0)                         # rising data gives a rising curve
    for xv, yv in zip(x, y):                                # every measured point is on the curve
        assert abs(np.interp(np.log10(xv), np.log10(xs), ys) - yv) < 1e-9


def test_smooth_curve_short_or_bad_input_is_passed_through():
    xs, ys = smooth_curve([1, 2], [3, 4])
    assert list(xs) == [1, 2] and list(ys) == [3, 4]
    xs, ys = smooth_curve([1, 1, 2], [3, 4, 5])             # duplicate x: no interpolation
    assert len(xs) == 3


def test_range_with_limits_keeps_far_limits_out_and_near_limits_in():
    data = [620e-6, 625e-6, 635e-6]                              # span 15 µH
    assert range_with_limits(data, [558e-6, 682e-6]) == (620e-6, 635e-6)     # ±10 % band is far: curve keeps its shape
    assert range_with_limits(data, [650e-6]) == (620e-6, 650e-6)             # a limit 1 span away comes into view
    assert range_with_limits([9.2e-6, 9.24e-6], [12e-6]) == (9.2e-6, 9.24e-6)
    assert range_with_limits([5.0, 5.0], [5.05]) == (5.0, 5.05)              # flat data still has a usable span


def test_unit_edit_round_trips_and_flags_missing_values():
    _app = QApplication.instance() or QApplication([])  # noqa: F841
    e = UnitEdit("H", required=True)
    assert e.value() is None and e.property("invalid") is True
    e.set_value(620e-6)
    assert (e.edit.text(), e.combo.currentData()) == ("620", "µ") and abs(e.value() - 620e-6) < 1e-15
    assert e.property("invalid") is False
    e.set_value(1.5e-3)
    assert (e.edit.text(), e.combo.currentData()) == ("1.5", "m")
    e.combo.setCurrentIndex(e.prefixes.index("n"))                # the drop-down changes the scale
    assert abs(e.value() - 1.5e-9) < 1e-24
    e.edit.setText("")
    assert e.value() is None and e.property("invalid") is True
    pct = UnitEdit("%", prefixes=("",), required=True)
    pct.set_value(2.5)
    assert pct.combo is None and pct.edit.text() == "2.5" and pct.value() == 2.5
