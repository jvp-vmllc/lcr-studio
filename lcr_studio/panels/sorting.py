"""Component sorting (meter comparator + software bins) and component matching."""
from __future__ import annotations

import time

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..engmath import fmt
from ..theme import SERIES, repolish, theme
from ..ut622e import ALARMS, PRIMARY_UNIT, SOUNDS, Reading
from ..widgets import Badge, Card, EngEdit, Segmented, export_table, field_label, make_plot, muted
from .measure import fmt_secondary


class StableDetector:
    """Counts a part once: when its reading is inside the presence window and has settled.

    It re-arms only after the reading leaves the window (part removed).
    """

    def __init__(self):
        self.buf = []
        self.counted = False

    def reset(self):
        self.buf = []
        self.counted = False

    def feed(self, value, nominal, presence_pct, stable_pct, k):
        if not nominal or abs(value / nominal - 1) * 100 > presence_pct:
            self.reset()
            return "absent", None
        self.buf = (self.buf + [value])[-k:]
        mean = sum(self.buf) / len(self.buf)
        if self.counted:
            return "counted", mean
        if len(self.buf) >= k and (max(self.buf) - min(self.buf)) / abs(mean) * 100 <= stable_pct:
            self.counted = True
            return "new", mean
        return "settling", mean


def _item(text, align=Qt.AlignRight | Qt.AlignVCenter):
    it = QTableWidgetItem(text)
    it.setTextAlignment(align)
    return it


def _detector_controls(grid, row):
    presence = QDoubleSpinBox()
    presence.setRange(1, 99)
    presence.setValue(50)
    presence.setSuffix(" %")
    presence.setToolTip("A part counts as inserted when the reading is within this window around nominal")
    stable = QDoubleSpinBox()
    stable.setRange(0.001, 10)
    stable.setDecimals(3)
    stable.setValue(0.2)
    stable.setSuffix(" %")
    stable.setToolTip("Maximum spread of the last N readings for the value to count as settled")
    nread = QSpinBox()
    nread.setRange(2, 20)
    nread.setValue(3)
    grid.addWidget(field_label("Presence window ±"), row, 0)
    grid.addWidget(presence, row, 1)
    grid.addWidget(field_label("Settled when spread ≤"), row + 1, 0)
    grid.addWidget(stable, row + 1, 1)
    grid.addWidget(field_label("over readings"), row + 2, 0)
    grid.addWidget(nread, row + 2, 1)
    return presence, stable, nread


# ============================================================== sorting ==

class SortingPanel(QWidget):
    def __init__(self, worker, parent=None):
        super().__init__(parent)
        self.worker = worker
        self.last: Reading | None = None
        self.unit = ""
        self._hw_seen = None
        self.det = StableDetector()
        self.counts = {}

        root = QHBoxLayout(self)
        root.setContentsMargins(6, 12, 12, 12)
        root.setSpacing(10)
        left = QVBoxLayout()
        left.setSpacing(10)

        # --- hardware comparator
        hw = Card("Meter comparator")
        g = QGridLayout()
        g.setVerticalSpacing(6)
        self.hw_on = QCheckBox("Tolerance mode on meter")
        g.addWidget(self.hw_on, 0, 0, 1, 3)
        self.hw_nom = EngEdit("e.g. 4.7u")
        use = QPushButton("Use reading")
        use.clicked.connect(lambda: self.last and self.hw_nom.set_value(self.last.primary, 5))
        g.addWidget(field_label("Nominal"), 1, 0)
        g.addWidget(self.hw_nom, 1, 1)
        g.addWidget(use, 1, 2)
        self.hw_tol = QSpinBox()
        self.hw_tol.setRange(1, 20)
        self.hw_tol.setSuffix(" %")
        g.addWidget(field_label("Tolerance ±"), 2, 0)
        g.addWidget(self.hw_tol, 2, 1)
        g.addWidget(field_label("Beep on"), 3, 0)
        self.hw_alarm = Segmented(ALARMS)
        g.addWidget(self.hw_alarm, 3, 1, 1, 2)
        g.addWidget(field_label("Sound"), 4, 0)
        self.hw_sound = Segmented([(s, s.title()) for s in SOUNDS])
        g.addWidget(self.hw_sound, 4, 1, 1, 2)
        self.hw_led = QCheckBox("Alarm LED")
        self.hw_counter = QCheckBox("Pass/fail counter")
        g.addWidget(self.hw_led, 5, 0)
        g.addWidget(self.hw_counter, 5, 1, 1, 2)
        hw.body.addLayout(g)
        self.hw_apply = QPushButton("Apply to meter")
        self.hw_apply.setObjectName("Accent")
        self.hw_apply.clicked.connect(self._apply_hw)
        hw.body.addWidget(self.hw_apply)
        self.hw_msg = muted("The meter compares the primary parameter only. While tolerance mode is on, "
                            "the primary parameter cannot be changed.")
        hw.body.addWidget(self.hw_msg)
        left.addWidget(hw)

        # --- software bins
        sw = Card("Software binning")
        g = QGridLayout()
        g.setVerticalSpacing(6)
        self.nom = EngEdit("e.g. 100n")
        self.nom.valueChanged.connect(lambda _: self._reset_counts())
        use2 = QPushButton("Use reading")
        use2.clicked.connect(lambda: self.last and self.nom.set_value(self.last.primary, 5))
        g.addWidget(field_label("Nominal"), 0, 0)
        g.addWidget(self.nom, 0, 1)
        g.addWidget(use2, 0, 2)
        self.bins_edit = QLineEdit("1, 2, 5, 10, 20")
        self.bins_edit.setToolTip("Bin limits in ±% — each part lands in the tightest bin it fits")
        self.bins_edit.editingFinished.connect(self._reset_counts)
        g.addWidget(field_label("Bins ±%"), 1, 0)
        g.addWidget(self.bins_edit, 1, 1, 1, 2)
        self.presence, self.stable, self.nread = _detector_controls(g, 2)
        sw.body.addLayout(g)
        self.counting = QCheckBox("Count parts (auto-detect insert / settle / remove)")
        self.counting.setChecked(True)
        sw.body.addWidget(self.counting)
        rst = QPushButton("Reset counts")
        rst.clicked.connect(self._reset_counts)
        exp = QPushButton("Export counts…")
        exp.clicked.connect(self._export)
        row = QHBoxLayout()
        row.addWidget(rst)
        row.addWidget(exp)
        sw.body.addLayout(row)
        left.addWidget(sw)
        left.addStretch(1)
        lw = QWidget()
        lw.setLayout(left)
        left.setContentsMargins(0, 0, 0, 0)
        lw.setFixedWidth(380)
        root.addWidget(lw)

        # --- right side
        right = QVBoxLayout()
        right.setSpacing(10)
        disp = Card("Classification")
        self.bin_text = QLabel("—")
        self.bin_text.setObjectName("BinText")
        self.bin_text.setAlignment(Qt.AlignCenter)
        self.dev_text = QLabel("")
        self.dev_text.setObjectName("MidValue")
        self.dev_text.setAlignment(Qt.AlignCenter)
        self.state_badge = Badge("SET A NOMINAL")
        self.state_badge.setAlignment(Qt.AlignCenter)
        disp.body.addWidget(self.bin_text)
        disp.body.addWidget(self.dev_text)
        hb = QHBoxLayout()
        hb.addStretch(1)
        hb.addWidget(self.state_badge)
        hb.addStretch(1)
        disp.body.addLayout(hb)
        right.addWidget(disp)

        cnt = Card("Counts")
        body = QHBoxLayout()
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Bin", "Limits", "Count", "Share"])
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        body.addWidget(self.table, 1)
        self.bar = make_plot("Parts", "", "", "")
        self.bar_item = pg.BarGraphItem(x=[], height=[], width=0.7)
        self.bar.addItem(self.bar_item)
        self.bar.getPlotItem().getViewBox().setMouseEnabled(False, False)
        self.bar.getPlotItem().getAxis("left").enableAutoSIPrefix(False)
        self.bar.getPlotItem().getViewBox().setLimits(yMin=0)
        body.addWidget(self.bar, 1)
        cnt.body.addLayout(body)
        self.total_label = muted("")
        cnt.body.addWidget(self.total_label)
        right.addWidget(cnt, 1)
        root.addLayout(right, 1)
        self._reset_counts()

    # ------------------------------------------------------------------
    def set_connected(self, on):
        self.hw_apply.setEnabled(on)

    def apply_settings(self, st):
        hw = tuple(st.get(k) for k in ("comp", "comp_nominal", "comp_tol", "alarm", "sound", "led", "counter"))
        if hw == self._hw_seen or hw[0] is None:
            return
        self._hw_seen = hw
        on, nom, tol, alarm, sound, led, counter = hw
        self.hw_on.setChecked(bool(on))
        self.unit = PRIMARY_UNIT.get(st.get("primary"), "")
        self.hw_nom.unit = self.unit
        self.nom.unit = self.unit
        self.hw_nom.set_value(nom if nom else None)
        self.hw_tol.setValue(int(round(tol or 5)))
        self.hw_alarm.set_value(alarm)
        self.hw_sound.set_value(sound)
        self.hw_led.setChecked(bool(led))
        self.hw_counter.setChecked(bool(counter))

    def _apply_hw(self):
        nom = self.hw_nom.value()
        vals = dict(on=self.hw_on.isChecked(), nom=nom, tol=self.hw_tol.value(),
                    alarm=self.hw_alarm.value(), sound=self.hw_sound.value(),
                    led=self.hw_led.isChecked(), counter=self.hw_counter.isChecked())
        if vals["on"] and not nom:
            self.hw_msg.setText("Enter a nominal value first.")
            return

        def fn(m, v=vals):
            if v["nom"]:
                m.set_comp_nominal(v["nom"])
            m.set_comp_tol(v["tol"])
            if v["alarm"]:
                m.set_alarm(v["alarm"])
            if v["sound"]:
                m.set_sound(v["sound"])
            m.set_led(v["led"])
            m.set_counter(v["counter"])
            m.set_comp(v["on"])
        self.worker.call(fn, tag="comp", refresh="full")
        self.hw_msg.setText("Applied.")

    # ------------------------------------------------------------------
    def _bins(self):
        try:
            vals = sorted({abs(float(x)) for x in self.bins_edit.text().replace(";", ",").split(",") if x.strip()})
        except ValueError:
            vals = []
        return [v for v in vals if v > 0]

    def _labels(self):
        bins = self._bins()
        rows = [(f"BIN {i + 1}", f"±{b:g} %") for i, b in enumerate(bins)]
        worst = bins[-1] if bins else 0
        rows += [("LOW", f"< −{worst:g} %"), ("HIGH", f"> +{worst:g} %")]
        return rows

    def classify(self, value, nominal):
        dev = (value / nominal - 1) * 100
        for i, b in enumerate(self._bins()):
            if abs(dev) <= b:
                return f"BIN {i + 1}", dev
        return ("LOW" if dev < 0 else "HIGH"), dev

    def _reset_counts(self):
        self.det.reset()
        self.counts = {label: 0 for label, _ in self._labels()}
        self._fill_counts()

    def _fill_counts(self):
        labels = self._labels()
        total = sum(self.counts.values())
        self.table.setRowCount(len(labels))
        for i, (label, lim) in enumerate(labels):
            n = self.counts.get(label, 0)
            self.table.setItem(i, 0, _item(label, Qt.AlignLeft | Qt.AlignVCenter))
            self.table.setItem(i, 1, _item(lim))
            self.table.setItem(i, 2, _item(str(n)))
            self.table.setItem(i, 3, _item(f"{n / total * 100:.1f} %" if total else "—"))
        self.table.resizeColumnsToContents()
        good = sum(n for k, n in self.counts.items() if k.startswith("BIN"))
        self.total_label.setText(f"Total {total} · in tolerance {good} · yield "
                                 f"{good / total * 100:.1f} %" if total else "No parts counted yet.")
        c = theme.c
        colors = [SERIES[2]] * (len(labels) - 2) + [c["bad"], c["bad"]]
        self.bar_item.setOpts(x=list(range(len(labels))), height=[self.counts.get(l, 0) for l, _ in labels],
                              brushes=[pg.mkBrush(col) for col in colors], pen=pg.mkPen(None))
        self.bar.getPlotItem().getAxis("bottom").setTicks([[(i, l) for i, (l, _) in enumerate(labels)]])
        self.bar.getPlotItem().setYRange(0, max([4] + list(self.counts.values())) * 1.15, padding=0)

    def _set_bin_color(self, key):
        c = theme.c
        color = c["text"] if key is None else c["good"] if key.startswith("BIN") else c["bad"]
        self.bin_text.setStyleSheet(f"color: {color};")

    def on_reading(self, r: Reading):
        self.last = r
        nominal = self.nom.value()
        if not nominal or r.overload:
            self.bin_text.setText("—")
            self._set_bin_color(None)
            self.dev_text.setText("")
            self.state_badge.setText("SET A NOMINAL" if not nominal else "OVERLOAD")
            self.state_badge.set_kind("")
            return
        key, dev = self.classify(r.primary, nominal)
        state, mean = self.det.feed(r.primary, nominal, self.presence.value(), self.stable.value(),
                                    self.nread.value())
        if state == "absent":
            self.bin_text.setText("—")
            self._set_bin_color(None)
            self.dev_text.setText(fmt(r.primary, r.punit))
            self.state_badge.setText("INSERT PART")
            self.state_badge.set_kind("")
            return
        self.bin_text.setText(key)
        self._set_bin_color(key)
        self.dev_text.setText(f"{dev:+.3f} %   ·   {fmt(r.primary, r.punit)}")
        if state == "new" and self.counting.isChecked():
            k, _ = self.classify(mean, nominal)
            self.counts[k] = self.counts.get(k, 0) + 1
            self._fill_counts()
        if state == "settling":
            self.state_badge.setText("SETTLING…")
            self.state_badge.set_kind("warn")
        else:
            self.state_badge.setText("COUNTED — REMOVE PART" if self.counting.isChecked() else "STABLE")
            self.state_badge.set_kind("good" if key.startswith("BIN") else "bad")

    def _export(self):
        labels = self._labels()
        total = sum(self.counts.values())
        rows = [[l, lim, self.counts.get(l, 0), (self.counts.get(l, 0) / total * 100) if total else None]
                for l, lim in labels]
        export_table(self, ["Bin", "Limits", "Count", "Share %"], rows, "sorting.xlsx")


# ============================================================= matching ==

class MatchingPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.recent: list[Reading] = []
        self.parts = []           # dicts: id, p, s, ptype, stype, punit, group
        self.det = StableDetector()
        self.next_id = 1

        root = QHBoxLayout(self)
        root.setContentsMargins(6, 12, 12, 12)
        root.setSpacing(10)
        left = QVBoxLayout()
        left.setSpacing(10)

        cap = Card("Capture")
        self.cap_btn = QPushButton("Capture part  (Enter)")
        self.cap_btn.setObjectName("Accent")
        self.cap_btn.clicked.connect(self.capture)
        cap.body.addWidget(self.cap_btn)
        cap.body.addWidget(muted("Captures the average of the last 3 readings. Or let the app capture "
                                 "automatically each time a part is inserted and settles:"))
        g = QGridLayout()
        self.auto = QCheckBox("Auto-capture")
        g.addWidget(self.auto, 0, 0, 1, 2)
        self.nom = EngEdit("needed for auto-capture")
        g.addWidget(field_label("Nominal"), 1, 0)
        g.addWidget(self.nom, 1, 1)
        self.presence, self.stable, self.nread = _detector_controls(g, 2)
        cap.body.addLayout(g)
        self.cap_state = Badge("IDLE")
        cap.body.addWidget(self.cap_state)
        left.addWidget(cap)

        mt = Card("Matching")
        g = QGridLayout()
        self.group_size = QSpinBox()
        self.group_size.setRange(2, 12)
        self.group_size.setValue(2)
        self.spread = QDoubleSpinBox()
        self.spread.setRange(0.001, 50)
        self.spread.setDecimals(3)
        self.spread.setValue(1.0)
        self.spread.setSuffix(" %")
        self.sec_spread = QDoubleSpinBox()
        self.sec_spread.setRange(0, 1000)
        self.sec_spread.setDecimals(2)
        self.sec_spread.setValue(0)
        self.sec_spread.setSuffix(" %")
        self.sec_spread.setSpecialValueText("ignore")
        self.sec_spread.setToolTip("Also require the secondary parameter (e.g. D or ESR) to match within this spread")
        g.addWidget(field_label("Parts per set"), 0, 0)
        g.addWidget(self.group_size, 0, 1)
        g.addWidget(field_label("Max primary spread"), 1, 0)
        g.addWidget(self.spread, 1, 1)
        g.addWidget(field_label("Max secondary spread"), 2, 0)
        g.addWidget(self.sec_spread, 2, 1)
        mt.body.addLayout(g)
        find = QPushButton("Find matched sets")
        find.setObjectName("Accent")
        find.clicked.connect(self.match)
        mt.body.addWidget(find)
        row = QHBoxLayout()
        rm = QPushButton("Remove selected")
        rm.clicked.connect(self._remove)
        clr = QPushButton("Clear all")
        clr.setObjectName("Danger")
        clr.clicked.connect(self._clear)
        exp = QPushButton("Export…")
        exp.clicked.connect(self._export)
        row.addWidget(rm)
        row.addWidget(clr)
        row.addWidget(exp)
        mt.body.addLayout(row)
        self.summary = muted("")
        mt.body.addWidget(self.summary)
        left.addWidget(mt)
        left.addStretch(1)
        lw = QWidget()
        lw.setLayout(left)
        left.setContentsMargins(0, 0, 0, 0)
        lw.setFixedWidth(360)
        root.addWidget(lw)

        tc = Card("Captured parts")
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["#", "Primary", "Secondary", "Δ nominal", "Set"])
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        tc.body.addWidget(self.table)
        root.addWidget(tc, 1)

    def on_reading(self, r: Reading):
        if r.overload:
            return
        self.recent = (self.recent + [r])[-3:]
        if not self.auto.isChecked():
            self.cap_state.setText("MANUAL")
            self.cap_state.set_kind("")
            return
        state, _ = self.det.feed(r.primary, self.nom.value(), self.presence.value(), self.stable.value(),
                                 self.nread.value())
        if state == "new":
            self.capture()
        self.cap_state.setText({"absent": "INSERT PART", "settling": "SETTLING…", "new": "CAPTURED",
                                "counted": "CAPTURED — REMOVE PART"}[state] if self.nom.value() else "SET NOMINAL")
        self.cap_state.set_kind({"settling": "warn", "new": "good", "counted": "good"}.get(state, ""))

    def capture(self):
        if not self.recent:
            return
        rs = [r for r in self.recent if (r.ptype, r.stype) == (self.recent[-1].ptype, self.recent[-1].stype)]
        last = rs[-1]
        p = sum(r.primary for r in rs) / len(rs)
        s = sum(r.secondary for r in rs) / len(rs) if last.stype else None
        self.parts.append(dict(id=self.next_id, p=p, s=s, ptype=last.ptype, stype=last.stype,
                               punit=last.punit, t=time.strftime("%H:%M:%S"), group=None))
        self.next_id += 1
        self._fill()

    def match(self):
        k = self.group_size.value()
        limit = self.spread.value()
        slimit = self.sec_spread.value()
        for part in self.parts:
            part["group"] = None
        pool = sorted([p for p in self.parts if p["p"] > 0], key=lambda p: p["p"])
        group = 0
        while len(pool) >= k:
            best = None
            for i in range(len(pool) - k + 1):
                win = pool[i:i + k]
                mean = sum(p["p"] for p in win) / k
                spread = (win[-1]["p"] - win[0]["p"]) / mean * 100
                if spread > limit or (best and spread >= best[0]):
                    continue
                if slimit and all(p["s"] is not None for p in win):
                    sv = [p["s"] for p in win]
                    smean = sum(sv) / k
                    if smean and (max(sv) - min(sv)) / abs(smean) * 100 > slimit:
                        continue
                best = (spread, i)
            if best is None:
                break
            group += 1
            for p in pool[best[1]:best[1] + k]:
                p["group"] = group
            del pool[best[1]:best[1] + k]
        matched = sum(1 for p in self.parts if p["group"])
        self.summary.setText(f"{group} matched set(s) · {matched} parts matched · "
                             f"{len(self.parts) - matched} unmatched")
        self._fill()

    def _fill(self):
        nominal = self.nom.value()
        self.table.setRowCount(len(self.parts))
        for i, part in enumerate(self.parts):
            sv, su = fmt_secondary(part["stype"], part["s"])
            cells = [str(part["id"]), f"{part['ptype']}  {fmt(part['p'], part['punit'])}",
                     f"{part['stype'] or ''}  {sv} {su}".strip(),
                     f"{(part['p'] / nominal - 1) * 100:+.3f} %" if nominal else "—",
                     f"Set {part['group']}" if part["group"] else ""]
            for j, text in enumerate(cells):
                it = _item(text, Qt.AlignLeft | Qt.AlignVCenter if j == 4 else Qt.AlignRight | Qt.AlignVCenter)
                if part["group"]:
                    col = pg.mkColor(SERIES[(part["group"] - 1) % len(SERIES)])
                    col.setAlpha(60)
                    it.setBackground(col)
                self.table.setItem(i, j, it)
        self.table.resizeColumnsToContents()
        self.table.scrollToBottom()

    def _remove(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.parts.pop(r)
        self._fill()

    def _clear(self):
        self.parts = []
        self.next_id = 1
        self.summary.setText("")
        self._fill()

    def _export(self):
        rows = [[p["id"], p["t"], p["ptype"], p["p"], p["punit"], p["stype"], p["s"], p["group"]] for p in self.parts]
        export_table(self, ["#", "Time", "Primary", "Value", "Unit", "Secondary", "Value", "Set"], rows,
                     "matching.xlsx")
