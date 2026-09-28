"""Live measurement: big readout, derived parameters, trend chart, histogram and statistics."""
from __future__ import annotations

import math
import time

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLabel, QPushButton,
                               QSplitter, QVBoxLayout, QWidget)

from ..engmath import eng_parts, fmt, params_from_z, z_from_measurement
from ..theme import SERIES, theme
from ..ut622e import FREQ_HZ, RANGES, SECONDARY_LABEL, Reading
from ..widgets import Badge, Card, field_label, make_plot, muted


def fmt_secondary(stype, v):
    """Secondary value text + unit, using the resolution the meter itself shows."""
    if stype is None or v is None:
        return "—", ""
    if stype == "D":
        return f"{v:.5f}", ""
    if stype == "Q":
        return f"{v:.5g}", ""
    if stype == "DEG":
        return f"{v:.2f}", "°"
    if stype == "RAD":
        return f"{v:.4f}", "rad"
    m, p = eng_parts(v, 5)
    return m, p + "Ω"


def fmt_derived(key, v):
    if v is None or (isinstance(v, float) and math.isinf(v)):
        return "—"
    if key == "θ":
        return f"{v:.3f} °"
    if key in ("D", "Q"):
        return f"{v:.5g}"
    unit = {"C": "F", "L": "H"}.get(key[0], "Ω")
    return fmt(v, unit)


WINDOWS = [("30 s", 30), ("1 min", 60), ("5 min", 300), ("15 min", 900), ("1 h", 3600), ("All", 0)]
MAX_POINTS = 200_000


class MeasurePanel(QWidget):
    snapshot = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.last: Reading | None = None
        self.ref: float | None = None
        self._cfg_key = None
        self._t0 = None
        self.t, self.p, self.s = [], [], []
        self._dirty = False
        self._rate_times = []

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 12, 12, 12)
        root.setSpacing(10)
        split = QSplitter(Qt.Vertical)
        split.setChildrenCollapsible(False)
        root.addWidget(split)

        top = QWidget()
        top_l = QHBoxLayout(top)
        top_l.setContentsMargins(0, 0, 0, 0)
        top_l.setSpacing(10)
        top_l.addWidget(self._build_readout(), 3)
        top_l.addWidget(self._build_derived(), 2)
        split.addWidget(top)
        split.addWidget(self._build_trend())
        split.setSizes([330, 420])

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._redraw)
        self._timer.start(100)
        theme.changed.connect(lambda c: self._restyle_curves())
        self._restyle_curves()

    # ---------------------------------------------------------- building --
    def _build_readout(self):
        card = Card("Primary")
        self.readout_card = card
        self.badges = {k: Badge("") for k in ("freq", "level", "speed", "equ", "range", "trig")}
        for b in self.badges.values():
            card.header.addWidget(b)
        big = QHBoxLayout()
        big.setSpacing(12)
        self.ptype = QLabel("—")
        self.ptype.setObjectName("BigType")
        self.pvalue = QLabel("—")
        self.pvalue.setObjectName("BigValue")
        self.pvalue.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.pvalue.setMinimumWidth(330)
        self.punit = QLabel("")
        self.punit.setObjectName("BigUnit")
        self.punit.setMinimumWidth(70)
        big.addWidget(self.ptype, 0, Qt.AlignBottom)
        big.addStretch(1)
        big.addWidget(self.pvalue)
        big.addWidget(self.punit, 0, Qt.AlignBottom)
        card.body.addLayout(big)

        sec = QHBoxLayout()
        sec.setSpacing(10)
        self.stype = QLabel("")
        self.stype.setObjectName("MidType")
        self.svalue = QLabel("")
        self.svalue.setObjectName("MidValue")
        self.svalue.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.sunit = QLabel("")
        self.sunit.setObjectName("MidUnit")
        self.sunit.setMinimumWidth(70)
        sec.addWidget(self.stype, 0, Qt.AlignBottom)
        sec.addStretch(1)
        sec.addWidget(self.svalue)
        sec.addWidget(self.sunit, 0, Qt.AlignBottom)
        card.body.addLayout(sec)

        info = QHBoxLayout()
        self.cmp_badge = Badge("")
        self.cmp_badge.hide()
        self.rel_label = muted("")
        self.rate_label = muted("")
        info.addWidget(self.cmp_badge)
        info.addWidget(self.rel_label, 1)
        info.addWidget(self.rate_label)
        card.body.addLayout(info)
        card.body.addStretch(1)

        btns = QHBoxLayout()
        self.hold = QPushButton("Hold  (H)")
        self.hold.setCheckable(True)
        self.hold.setToolTip("Freeze the readout; logging and charts continue")
        self.rel = QPushButton("Relative  (R)")
        self.rel.setCheckable(True)
        self.rel.setToolTip("Show the change from the current reading")
        self.rel.toggled.connect(self._toggle_rel)
        snap = QPushButton("Snapshot to log  (S)")
        snap.clicked.connect(lambda: self.last and self.snapshot.emit(self.last))
        clear = QPushButton("Reset statistics")
        clear.clicked.connect(self.reset_trend)
        for b in (self.hold, self.rel, snap, clear):
            btns.addWidget(b)
        card.body.addLayout(btns)
        return card

    def _build_derived(self):
        card = Card("Derived parameters")
        self.derived_note = muted("")
        card.body.addWidget(self.derived_note)
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        self.derived = {}
        keys = [("|Z|", "Impedance magnitude"), ("θ", "Phase angle"), ("Rs", "Series resistance (ESR)"),
                ("Rp", "Parallel resistance"), ("Xs", "Series reactance"), ("Xp", "Parallel reactance"),
                ("Cs", "Series capacitance"), ("Cp", "Parallel capacitance"), ("Ls", "Series inductance"),
                ("Lp", "Parallel inductance"), ("D", "Dissipation factor"), ("Q", "Quality factor")]
        for i, (k, tip) in enumerate(keys):
            name = field_label(k)
            name.setToolTip(tip)
            val = QLabel("—")
            val.setObjectName("DerivedValue")
            val.setTextInteractionFlags(Qt.TextSelectableByMouse)
            r, c = divmod(i, 2)
            grid.addWidget(name, r, c * 2)
            grid.addWidget(val, r, c * 2 + 1)
            self.derived[k] = val
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        card.body.addLayout(grid)
        card.body.addStretch(1)
        return card

    def _build_trend(self):
        card = Card("Trend")
        self.window = QComboBox()
        for label, secs in WINDOWS:
            self.window.addItem(label, secs)
        self.window.setCurrentIndex(1)
        self.window.currentIndexChanged.connect(lambda: self._mark())
        self.show_sec = QCheckBox("Secondary")
        self.show_sec.setChecked(True)
        self.show_sec.toggled.connect(lambda on: (self.splot.setVisible(on), self._mark()))
        self.show_mean = QCheckBox("Mean line")
        self.show_mean.setChecked(True)
        self.show_mean.toggled.connect(lambda: self._mark())
        autoscale = QPushButton("Autoscale")
        autoscale.clicked.connect(lambda: (self.pplot.enableAutoRange(), self.splot.enableAutoRange(),
                                           self.hplot.enableAutoRange()))
        card.header.addWidget(field_label("Window"))
        card.header.addWidget(self.window)
        card.header.addWidget(self.show_sec)
        card.header.addWidget(self.show_mean)
        card.header.addWidget(autoscale)

        body = QHBoxLayout()
        charts = QVBoxLayout()
        charts.setSpacing(4)
        self.pplot = make_plot("Primary", "", "Time", "s")
        self.splot = make_plot("Secondary", "", "Time", "s")
        self.splot.setXLink(self.pplot)
        self.pcurve = self.pplot.plot([], [])
        self.scurve = self.splot.plot([], [])
        self.mean_line = pg.InfiniteLine(angle=0, movable=False)
        self.ref_line = pg.InfiniteLine(angle=0, movable=False)
        self.pplot.addItem(self.mean_line)
        self.pplot.addItem(self.ref_line)
        charts.addWidget(self.pplot, 3)
        charts.addWidget(self.splot, 2)
        body.addLayout(charts, 1)

        side = QVBoxLayout()
        self.hplot = make_plot("Count", "", "Primary", "")
        self.hplot.setFixedWidth(270)
        self.hist = pg.BarGraphItem(x=[], height=[], width=1)
        self.hplot.addItem(self.hist)
        side.addWidget(self.hplot, 1)
        grid = QGridLayout()
        grid.setVerticalSpacing(2)
        self.stats = {}
        for i, k in enumerate(["N", "Mean", "σ", "σ %", "Min", "Max", "P–P", "Rate"]):
            lab = field_label(k)
            val = QLabel("—")
            val.setObjectName("StatValue")
            val.setTextInteractionFlags(Qt.TextSelectableByMouse)
            grid.addWidget(lab, (i // 2) * 2, i % 2)
            grid.addWidget(val, (i // 2) * 2 + 1, i % 2)
            self.stats[k] = val
        side.addLayout(grid)
        body.addLayout(side)
        card.body.addLayout(body, 1)
        return card

    def _restyle_curves(self):
        c = theme.c
        self.pcurve.setPen(pg.mkPen(SERIES[0], width=2))
        self.scurve.setPen(pg.mkPen(SERIES[1], width=2))
        self.mean_line.setPen(pg.mkPen(c["muted"], width=1, style=Qt.DashLine))
        self.ref_line.setPen(pg.mkPen(c["warn"], width=1, style=Qt.DotLine))
        self.hist.setOpts(brush=pg.mkBrush(SERIES[0] + "b0"), pen=pg.mkPen(None))

    # ----------------------------------------------------------- updates --
    def apply_settings(self, st: dict):
        f = st.get("frequency")
        dcr = st.get("primary") == "DCR"
        self.badges["freq"].setText("DC" if dcr else (f or "?"))
        self.badges["level"].setText("1 V DC" if dcr else (st.get("level") or "?"))
        self.badges["speed"].setText(st.get("speed") or "?")
        self.badges["equ"].setText({"SER": "SERIES", "PAR": "PARALLEL"}.get(st.get("equivalent"), "?"))
        self.badges["equ"].setVisible(not dcr)
        if st.get("range_auto"):
            self.badges["range"].setText("AUTO RANGE")
            self.badges["range"].set_kind("")
        else:
            r = st.get("range")
            self.badges["range"].setText(f"HOLD {RANGES[r]}" if isinstance(r, int) and r < 5 else "HOLD")
            self.badges["range"].set_kind("warn")
        man = st.get("trigger") == "MAN"
        self.badges["trig"].setText("SINGLE" if man else "CONT")
        self.badges["trig"].set_kind("accent" if man else "")

    def add_reading(self, r: Reading):
        self.last = r
        key = (r.ptype, r.stype, r.frequency, r.level, r.equivalent)
        if key != self._cfg_key:
            self._cfg_key = key
            self.reset_trend()
            self.pplot.setLabel("left", r.ptype, units=r.punit)
            sunit = r.sunit if r.stype not in ("DEG", "RAD") else ""
            self.splot.getPlotItem().getAxis("left").enableAutoSIPrefix(bool(sunit))
            self.splot.setLabel("left", SECONDARY_LABEL.get(r.stype, "—"), units=sunit)
            self.hplot.setLabel("bottom", r.ptype, units=r.punit)
            if self.rel.isChecked():
                self.rel.setChecked(False)

        now = time.monotonic()
        self._rate_times = [x for x in self._rate_times if now - x < 3.0] + [now]
        if not self.hold.isChecked():
            self._show(r)
        if not r.overload:
            if self._t0 is None:
                self._t0 = r.t
            if self.t and r.t - self._t0 - self.t[-1] > 2.5:
                self.t.append(self.t[-1])
                self.p.append(math.nan)
                self.s.append(math.nan)
            self.t.append(r.t - self._t0)
            self.p.append(r.primary)
            self.s.append(r.secondary if r.secondary is not None else math.nan)
            if len(self.t) > MAX_POINTS:
                del self.t[:10_000], self.p[:10_000], self.s[:10_000]
            self._dirty = True

    def _show(self, r: Reading):
        self.ptype.setText(r.ptype)
        if r.overload:
            self.pvalue.setText("OL")
            self.punit.setText(r.punit)
        else:
            m, pre = eng_parts(r.primary, 5)
            self.pvalue.setText(m)
            self.punit.setText(pre + r.punit)
        self.stype.setText(SECONDARY_LABEL.get(r.stype, "") if r.stype else "")
        sv, su = fmt_secondary(r.stype, r.secondary)
        self.svalue.setText(sv if r.stype else "")
        self.sunit.setText(su)

        if r.compare:
            self.cmp_badge.setText(r.compare)
            self.cmp_badge.set_kind("good" if r.compare == "PASS" else "bad")
            self.cmp_badge.show()
        else:
            self.cmp_badge.hide()

        if self.ref is not None and not r.overload and self.ref != 0:
            d = r.primary - self.ref
            self.rel_label.setText(f"Δ {fmt(d, r.punit)}   ({(r.primary / self.ref - 1) * 100:+.3f} %)"
                                   f"   ref {fmt(self.ref, r.punit)}")
        else:
            self.rel_label.setText("")

        f = FREQ_HZ.get(r.frequency) if r.ptype != "DCR" else None
        z = None if r.overload else z_from_measurement(r.ptype, r.primary, r.stype, r.secondary, f, r.equivalent)
        if z is None:
            for v in self.derived.values():
                v.setText("—")
            self.derived_note.setText("Not available for DCR." if r.ptype == "DCR"
                                      else "Not available for this parameter pair.")
        else:
            params = params_from_z(z, f)
            for k, lab in self.derived.items():
                lab.setText(fmt_derived(k, params.get(k)))
            self.derived_note.setText(f"From {r.ptype} + {SECONDARY_LABEL.get(r.stype)} at {r.frequency}, "
                                      f"{'series' if r.equivalent == 'SER' else 'parallel'} model")

    def _toggle_rel(self, on):
        self.ref = self.last.primary if on and self.last and not self.last.overload else None
        if on and self.ref is None:
            self.rel.setChecked(False)
        self._mark()

    def reset_trend(self):
        self.t, self.p, self.s = [], [], []
        self._t0 = None
        self._mark()

    def _mark(self):
        self._dirty = True

    def _redraw(self):
        n_rate = len(self._rate_times)
        rate = (n_rate - 1) / (self._rate_times[-1] - self._rate_times[0]) if n_rate > 2 else 0.0
        self.rate_label.setText(f"{rate:.1f} readings/s" if rate else "")
        if not self._dirty:
            return
        self._dirty = False
        t = np.asarray(self.t)
        p = np.asarray(self.p)
        s = np.asarray(self.s)
        win = self.window.currentData()
        if win and len(t):
            sel = t >= t[-1] - win
            t, p, s = t[sel], p[sel], s[sel]
        self.pcurve.setData(t, p, connect="finite")
        p = p[np.isfinite(p)]
        if self.show_sec.isChecked():
            self.scurve.setData(t, s, connect="finite")
        unit = self.last.punit if self.last else ""
        self.stats["Rate"].setText(f"{rate:.1f}/s")
        self.stats["N"].setText(str(len(p)))
        if len(p):
            mean, sd = float(np.mean(p)), float(np.std(p, ddof=1)) if len(p) > 1 else 0.0
            self.stats["Mean"].setText(fmt(mean, unit))
            self.stats["σ"].setText(fmt(sd, unit, 3))
            self.stats["σ %"].setText(f"{abs(sd / mean) * 100:.4f}" if mean else "—")
            self.stats["Min"].setText(fmt(float(p.min()), unit))
            self.stats["Max"].setText(fmt(float(p.max()), unit))
            self.stats["P–P"].setText(fmt(float(p.max() - p.min()), unit, 3))
            self.mean_line.setPos(mean)
            self.mean_line.setVisible(self.show_mean.isChecked())
            bins = int(min(40, max(5, math.sqrt(len(p)))))
            if p.max() > p.min():
                h, edges = np.histogram(p, bins=bins)
                self.hist.setOpts(x=(edges[:-1] + edges[1:]) / 2, height=h, width=(edges[1] - edges[0]) * 0.9)
            else:
                self.hist.setOpts(x=[p[0]], height=[len(p)], width=abs(p[0]) * 1e-4 or 1e-12)
        else:
            for k in ("Mean", "σ", "σ %", "Min", "Max", "P–P"):
                self.stats[k].setText("—")
            self.mean_line.setVisible(False)
            self.hist.setOpts(x=[], height=[], width=1)
        self.ref_line.setVisible(self.ref is not None)
        if self.ref is not None:
            self.ref_line.setPos(self.ref)
