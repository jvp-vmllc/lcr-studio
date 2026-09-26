"""Flyback transformer test: guided Lp / Llk / DCR / secondary measurements and a one-page report."""
from __future__ import annotations

import json
import math
from datetime import datetime

import pyqtgraph as pg
from PySide6.QtCore import QPointF, QRectF, QSettings, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QGridLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton, QScrollArea,
                               QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import __version__
from ..engmath import fmt, parse_eng
from ..flyback import (MAX_SECONDARIES, FlybackProfile, Winding, build_steps, evaluate, hz_label, run_step, v_label)
from ..report import LEVEL_COLORS, default_meta, export_pdf, render_image
from ..theme import repolish, theme
from ..ut622e import FREQ_HZ, FREQUENCIES, LEVELS
from ..widgets import Badge, Card, EngEdit, Segmented, field_label, make_plot, muted

STATUS_KIND = {"done": "good", "running": "accent", "aborted": "warn", "error": "bad", "pending": ""}


# ------------------------------------------------------------ wiring diagram --

class WiringDiagram(QWidget):
    """Transformer symbol showing where the meter connects and which windings are shorted."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.primary = "Primary"
        self.secondaries = ["Secondary"]
        self.measured = -1
        self.shorted: list[int] = []
        self.setMinimumSize(330, 210)
        theme.changed.connect(lambda _c: self.update())

    def set_state(self, primary, secondaries, measured, shorted):
        self.primary, self.secondaries = primary, secondaries
        self.measured, self.shorted = measured, shorted
        self.update()

    def _coil(self, p, x, y0, y1, facing_right, pen):
        n = 4
        h = (y1 - y0) / n
        path = QPainterPath(QPointF(x, y0))
        for i in range(n):
            r = QRectF(x - h / 2, y0 + i * h, h, h)
            path.arcTo(r, 90, -180 if facing_right else 180)
        p.setPen(pen)
        p.drawPath(path)

    def paintEvent(self, _ev):
        c = theme.c
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        cx = w / 2
        muted_pen = QPen(QColor(c["muted"]), 2)
        accent_pen = QPen(QColor(c["accent"]), 3)
        short_pen = QPen(QColor(c["warn"]), 4, Qt.SolidLine, Qt.RoundCap)
        # core
        p.setPen(QPen(QColor(c["border"]), 3))
        p.drawLine(QPointF(cx - 5, 18), QPointF(cx - 5, h - 18))
        p.drawLine(QPointF(cx + 5, 18), QPointF(cx + 5, h - 18))
        font = p.font()
        font.setPointSizeF(8.5)
        p.setFont(font)

        def terminals(x_coil, y0, y1, x_pin, pen):
            p.setPen(pen)
            p.drawLine(QPointF(x_coil, y0), QPointF(x_pin, y0))
            p.drawLine(QPointF(x_coil, y1), QPointF(x_pin, y1))
            p.setBrush(QColor(c["surface"]))
            for yy in (y0, y1):
                p.drawEllipse(QPointF(x_pin, yy), 4, 4)
            p.setBrush(Qt.NoBrush)

        def meter(x_pin, y0, y1, left):
            bx = x_pin - 58 if left else x_pin + 14
            box = QRectF(bx, (y0 + y1) / 2 - 16, 44, 32)
            p.setPen(QPen(QColor(c["accent"]), 1.5))
            p.setBrush(QColor(c["accent"]))
            p.drawRoundedRect(box, 6, 6)
            p.setBrush(Qt.NoBrush)
            p.setPen(QColor("#ffffff"))
            p.drawText(box, Qt.AlignCenter, "LCR")
            edge = box.right() if left else box.left()
            p.setPen(QPen(QColor("#ef4444"), 2))
            p.drawLine(QPointF(edge, box.top() + 8), QPointF(x_pin, y0))
            p.setPen(QPen(QColor(c["text"]), 2))
            p.drawLine(QPointF(edge, box.bottom() - 8), QPointF(x_pin, y1))

        # primary
        py0, py1 = h * 0.2, h * 0.8
        pri_on = self.measured == -1
        self._coil(p, cx - 26, py0, py1, False, accent_pen if pri_on else muted_pen)
        terminals(cx - 26, py0, py1, cx - 70, accent_pen if pri_on else muted_pen)
        p.setPen(QColor(c["text"] if pri_on else c["muted"]))
        p.drawText(QRectF(cx - 140, py1 + 6, 110, 16), Qt.AlignRight, self.primary)
        if pri_on:
            meter(cx - 70, py0, py1, True)

        # secondaries
        n = max(1, len(self.secondaries))
        span = (h - 36) / n
        for i, name in enumerate(self.secondaries):
            y0 = 18 + i * span + span * 0.18
            y1 = 18 + (i + 1) * span - span * 0.18
            on = self.measured == i
            pen = accent_pen if on else muted_pen
            self._coil(p, cx + 26, y0, y1, True, pen)
            terminals(cx + 26, y0, y1, cx + 70, pen)
            if i in self.shorted:
                p.setPen(short_pen)
                p.drawLine(QPointF(cx + 78, y0), QPointF(cx + 78, y1))
                p.drawLine(QPointF(cx + 70, y0), QPointF(cx + 78, y0))
                p.drawLine(QPointF(cx + 70, y1), QPointF(cx + 78, y1))
                p.setPen(QColor(c["warn"]))
                p.drawText(QRectF(cx + 84, (y0 + y1) / 2 - 8, 60, 16), Qt.AlignLeft | Qt.AlignVCenter, "SHORT")
            elif on:
                meter(cx + 70, y0, y1, False)
            else:
                p.setPen(QColor(c["muted"]))
                p.drawText(QRectF(cx + 84, (y0 + y1) / 2 - 8, 60, 16), Qt.AlignLeft | Qt.AlignVCenter, "open")
            p.setPen(QColor(c["text"] if on else c["muted"]))
            p.drawText(QRectF(cx + 12, y1 + 1, 150, 14), Qt.AlignLeft, name)
        p.end()


# ------------------------------------------------------------ report preview --

class ReportPreview(QDialog):
    def __init__(self, image, on_export, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Report preview — US Letter")
        self.resize(900, 1000)
        lay = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(False)
        scroll.setAlignment(Qt.AlignHCenter)
        lab = QLabel()
        lab.setPixmap(QPixmap.fromImage(image))
        lab.setStyleSheet("background: white; border: 1px solid #ccd;")
        scroll.setWidget(lab)
        lay.addWidget(scroll, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        exp = QPushButton("Export PDF…")
        exp.setObjectName("Accent")
        exp.clicked.connect(lambda: (on_export(), self.accept()))
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        row.addWidget(exp)
        row.addWidget(close)
        lay.addLayout(row)


# ------------------------------------------------------------------ panel --

class FlybackPanel(QWidget):
    TAG = "flyback"

    def __init__(self, worker, settings: QSettings | None = None, parent=None):
        super().__init__(parent)
        self.worker = worker
        self.qs = settings
        self.results: dict = {}
        self.status: dict = {}
        self.running_key = None
        self.connected = False
        self.meta = {"meter": "—", "speed": "?", "correction": "—"}
        self._loading = False

        root = QHBoxLayout(self)
        root.setContentsMargins(6, 12, 12, 12)
        root.setSpacing(10)
        root.addWidget(self._build_setup())
        split = QSplitter(Qt.Vertical)
        split.setChildrenCollapsible(False)
        split.addWidget(self._build_steps())
        split.addWidget(self._build_results())
        split.setSizes([380, 470])
        root.addWidget(split, 1)

        self._load_saved_profile()
        self._rebuild_steps()
        self.set_connected(False)

    # ============================================================ setup ==
    def _build_setup(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFixedWidth(356)
        inner = QWidget()
        scroll.setWidget(inner)
        col = QVBoxLayout(inner)
        col.setContentsMargins(0, 0, 8, 0)
        col.setSpacing(10)

        unit = Card("Unit under test")
        g = QGridLayout()
        g.setVerticalSpacing(6)
        self.part = QLineEdit()
        self.part.setPlaceholderText("e.g. FBT-EE25-12V")
        self.desc = QLineEdit()
        self.desc.setPlaceholderText("e.g. 65 W flyback, 12 V / 5.4 A")
        self.serial = QLineEdit()
        self.serial.setPlaceholderText("serial number or lot")
        self.operator = QLineEdit()
        self.notes = QLineEdit()
        self.notes.setPlaceholderText("printed on the report")
        for i, (lab, wdg) in enumerate([("Part number", self.part), ("Description", self.desc),
                                        ("Serial / lot", self.serial), ("Operator", self.operator),
                                        ("Notes", self.notes)]):
            g.addWidget(field_label(lab), i, 0)
            g.addWidget(wdg, i, 1)
        unit.body.addLayout(g)
        row = QHBoxLayout()
        load = QPushButton("Load profile…")
        load.clicked.connect(self._load_profile_file)
        save = QPushButton("Save profile…")
        save.clicked.connect(self._save_profile_file)
        row.addWidget(load)
        row.addWidget(save)
        unit.body.addLayout(row)
        col.addWidget(unit)

        wind = Card("Windings")
        g = QGridLayout()
        self.pri_name = QLineEdit("Primary")
        self.pri_pins = QLineEdit("1-3")
        self.pri_pins.setMaximumWidth(64)
        self.pri_pins.setPlaceholderText("pins")
        self.pri_dcr = EngEdit("optional, e.g. 0.5", "Ω")
        g.addWidget(field_label("Primary"), 0, 0)
        g.addWidget(self.pri_name, 0, 1)
        g.addWidget(self.pri_pins, 0, 2)
        g.addWidget(field_label("DCR max"), 1, 0)
        g.addWidget(self.pri_dcr, 1, 1, 1, 2)
        wind.body.addLayout(g)
        wind.body.addWidget(field_label("Other windings (all shorted for Llk)"))
        self.sec_table = QTableWidget(0, 3)
        self.sec_table.setHorizontalHeaderLabels(["Name", "Pins", "DCR max"])
        self.sec_table.verticalHeader().hide()
        hh = self.sec_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.Fixed)
        hh.setSectionResizeMode(2, QHeaderView.Fixed)
        hh.resizeSection(1, 58)
        hh.resizeSection(2, 86)
        self.sec_table.setFixedHeight(140)
        self.sec_table.itemChanged.connect(lambda _i: self._profile_changed())
        wind.body.addWidget(self.sec_table)
        row = QHBoxLayout()
        add = QPushButton("+ Add winding")
        add.clicked.connect(lambda: self._add_secondary(Winding(f"Winding {self.sec_table.rowCount() + 1}")))
        rm = QPushButton("Remove")
        rm.clicked.connect(self._remove_secondary)
        row.addWidget(add)
        row.addWidget(rm)
        wind.body.addLayout(row)
        col.addWidget(wind)

        spec = Card("Specification")
        g = QGridLayout()
        g.setVerticalSpacing(6)
        self.spec_freq = QComboBox()
        for f in FREQUENCIES:
            self.spec_freq.addItem(hz_label(f), f)
        self.spec_level = QComboBox()
        for lv in LEVELS:
            self.spec_level.addItem(v_label(lv), lv)
        self.lp_nom = EngEdit("e.g. 620u", "H")
        self.lp_tol = QDoubleSpinBox()
        self.lp_tol.setRange(0.1, 50)
        self.lp_tol.setSuffix(" %")
        self.lp_tol.setValue(10)
        self.llk_max = EngEdit("e.g. 15u", "H")
        self.llk_pct = EngEdit("e.g. 2.5 (optional)")
        self.lp_equ = Segmented([("SER", "Series"), ("PAR", "Parallel")])
        self.llk_equ = Segmented([("SER", "Series"), ("PAR", "Parallel")])
        rows = [("Test frequency", self.spec_freq), ("Test level", self.spec_level), ("Lp nominal", self.lp_nom),
                ("Lp tolerance ±", self.lp_tol), ("Llk max", self.llk_max), ("Llk / Lp max (%)", self.llk_pct),
                ("Lp circuit model", self.lp_equ), ("Llk circuit model", self.llk_equ)]
        for i, (lab, wdg) in enumerate(rows):
            g.addWidget(field_label(lab), i, 0)
            g.addWidget(wdg, i, 1)
        spec.body.addLayout(g)
        spec.body.addWidget(muted("Limits are checked at the test frequency and level. Leave a limit empty to "
                                  "report the value without pass/fail."))
        col.addWidget(spec)

        mat = Card("Test matrix")
        mat.body.addWidget(field_label("Frequencies"))
        self.freq_boxes = {}
        g = QGridLayout()
        for i, f in enumerate(FREQUENCIES):
            cb = QCheckBox(hz_label(f))
            cb.setChecked(True)
            self.freq_boxes[f] = cb
            g.addWidget(cb, i // 3, i % 3)
        mat.body.addLayout(g)
        mat.body.addWidget(field_label("Test levels"))
        self.level_boxes = {}
        row = QHBoxLayout()
        for lv in LEVELS:
            cb = QCheckBox(v_label(lv))
            cb.setChecked(True)
            self.level_boxes[lv] = cb
            row.addWidget(cb)
        mat.body.addLayout(row)
        g = QGridLayout()
        self.settle = QSpinBox()
        self.settle.setRange(0, 20)
        self.settle.setValue(2)
        self.navg = QSpinBox()
        self.navg.setRange(1, 50)
        self.navg.setValue(5)
        g.addWidget(field_label("Settle (discard)"), 0, 0)
        g.addWidget(self.settle, 0, 1)
        g.addWidget(field_label("Average"), 1, 0)
        g.addWidget(self.navg, 1, 1)
        mat.body.addLayout(g)
        self.sec_full = QCheckBox("Full matrix for other windings")
        self.sec_full.setToolTip("Measure secondary windings at every frequency; by default only the test frequency is used")
        mat.body.addWidget(self.sec_full)
        col.addWidget(mat)
        col.addStretch(1)

        for w in (self.part, self.desc, self.pri_name, self.pri_pins):
            w.textChanged.connect(lambda _t: self._profile_changed())
        for w in (self.pri_dcr, self.lp_nom, self.llk_max, self.llk_pct):
            w.valueChanged.connect(lambda _v: self._profile_changed())
        for w in (self.spec_freq, self.spec_level):
            w.currentIndexChanged.connect(lambda _i: self._profile_changed())
        for w in (self.lp_tol,):
            w.valueChanged.connect(lambda _v: self._profile_changed())
        for w in (self.settle, self.navg):
            w.valueChanged.connect(lambda _v: self._profile_changed())
        for seg in (self.lp_equ, self.llk_equ):
            seg.changed.connect(lambda _v: self._profile_changed())
        for cb in list(self.freq_boxes.values()) + list(self.level_boxes.values()) + [self.sec_full]:
            cb.toggled.connect(lambda _on: self._profile_changed())
        return scroll

    # ============================================================ steps ==
    def _build_steps(self):
        card = Card("Test sequence")
        self.verdict_badge = Badge("NOT TESTED")
        card.header.addWidget(self.verdict_badge)
        body = QHBoxLayout()
        left = QVBoxLayout()
        self.step_table = QTableWidget(0, 3)
        self.step_table.setHorizontalHeaderLabels(["Step", "Status", "Result"])
        self.step_table.verticalHeader().hide()
        self.step_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.step_table.setSelectionMode(QTableWidget.SingleSelection)
        self.step_table.setEditTriggers(QTableWidget.NoEditTriggers)
        hh = self.step_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.step_table.currentCellChanged.connect(lambda *_: self._show_step())
        left.addWidget(self.step_table, 1)
        row = QHBoxLayout()
        self.run_btn = QPushButton("Run selected step")
        self.run_btn.setObjectName("Accent")
        self.run_btn.clicked.connect(self.run_selected)
        self.abort_btn = QPushButton("Abort")
        self.abort_btn.setEnabled(False)
        self.abort_btn.clicked.connect(self.worker.abort_job)
        self.new_btn = QPushButton("New unit")
        self.new_btn.setToolTip("Clear results for the next transformer (keeps the profile)")
        self.new_btn.clicked.connect(self.new_unit)
        row.addWidget(self.run_btn, 1)
        row.addWidget(self.abort_btn)
        row.addWidget(self.new_btn)
        left.addLayout(row)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        left.addWidget(self.bar)
        self.progress_text = muted("")
        left.addWidget(self.progress_text)
        body.addLayout(left, 5)

        right = QVBoxLayout()
        self.diagram = WiringDiagram()
        self.diagram.setMinimumSize(300, 190)
        right.addWidget(self.diagram, 1)
        self.instruction = QLabel("")
        self.instruction.setWordWrap(True)
        self.instruction.setMinimumHeight(64)
        self.instruction.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        right.addWidget(self.instruction)
        self.hint = muted("Tip: run open/short correction on the meter with the same leads before testing.")
        right.addWidget(self.hint)
        body.addLayout(right, 4)
        card.body.addLayout(body, 1)
        card.setMinimumHeight(360)
        return card

    # ========================================================== results ==
    def _build_results(self):
        card = Card("Results")
        prev = QPushButton("Preview report")
        prev.clicked.connect(self.preview)
        pdf = QPushButton("Export PDF report…")
        pdf.setObjectName("Accent")
        pdf.clicked.connect(self.export_pdf)
        xls = QPushButton("Export data…")
        xls.clicked.connect(self.export_data)
        for b in (prev, xls, pdf):
            card.header.addWidget(b)
        body = QHBoxLayout()
        charts = QVBoxLayout()
        self.lp_plot = make_plot("Lp", "H", "Frequency", "", log_x=True)
        self.llk_plot = make_plot("Llk", "H", "Frequency", "", log_x=True)
        ticks = [[(math.log10(FREQ_HZ[f]), f.replace("Hz", "")) for f in FREQUENCIES if f != "120Hz"],
                 [(math.log10(120), "")]]
        for plot in (self.lp_plot, self.llk_plot):
            ax = plot.getPlotItem().getAxis("bottom")
            ax.enableAutoSIPrefix(False)
            ax.setTicks(ticks)
            plot.getPlotItem().setLabel("bottom", "Frequency (Hz)")
            plot.getPlotItem().setXRange(math.log10(90), math.log10(110e3), padding=0.02)
            plot.getPlotItem().addLegend(offset=(8, 4), colCount=3)
        charts.addWidget(self.lp_plot)
        charts.addWidget(self.llk_plot)
        body.addLayout(charts, 4)
        self.summary = QTableWidget(0, 4)
        self.summary.setHorizontalHeaderLabels(["Parameter", "Measured", "Limit", ""])
        self.summary.setWordWrap(False)
        self.summary.verticalHeader().hide()
        self.summary.setEditTriggers(QTableWidget.NoEditTriggers)
        self.summary.setAlternatingRowColors(True)
        hh = self.summary.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for i in (1, 2, 3):
            hh.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        body.addWidget(self.summary, 5)
        card.body.addLayout(body, 1)
        return card

    # ======================================================== profile I/O ==
    def profile(self) -> FlybackProfile:
        secs = []
        for r in range(self.sec_table.rowCount()):
            name = (self.sec_table.item(r, 0).text() if self.sec_table.item(r, 0) else "").strip() or f"Winding {r + 1}"
            pins = self.sec_table.item(r, 1).text().strip() if self.sec_table.item(r, 1) else ""
            try:
                dmax = parse_eng(self.sec_table.item(r, 2).text()) if self.sec_table.item(r, 2) and \
                    self.sec_table.item(r, 2).text().strip() else None
            except ValueError:
                dmax = None
            secs.append(Winding(name, pins, dmax))
        return FlybackProfile(
            part_number=self.part.text().strip(), description=self.desc.text().strip(),
            primary=Winding(self.pri_name.text().strip() or "Primary", self.pri_pins.text().strip(), self.pri_dcr.value()),
            secondaries=secs, spec_freq=self.spec_freq.currentData(), spec_level=self.spec_level.currentData(),
            lp_nom=self.lp_nom.value(), lp_tol=self.lp_tol.value(), llk_max=self.llk_max.value(),
            llk_pct_max=self.llk_pct.value(), lp_equ=self.lp_equ.value() or "SER", llk_equ=self.llk_equ.value() or "SER",
            freqs=[f for f, cb in self.freq_boxes.items() if cb.isChecked()],
            levels=[lv for lv, cb in self.level_boxes.items() if cb.isChecked()],
            settle=self.settle.value(), navg=self.navg.value(), sec_full_matrix=self.sec_full.isChecked())

    def set_profile(self, p: FlybackProfile):
        self._loading = True
        try:
            self.part.setText(p.part_number)
            self.desc.setText(p.description)
            self.pri_name.setText(p.primary.name)
            self.pri_pins.setText(p.primary.pins)
            self.pri_dcr.set_value(p.primary.dcr_max, 4)
            self.sec_table.setRowCount(0)
            for w in p.secondaries:
                self._add_secondary(w)
            self.spec_freq.setCurrentIndex(max(0, self.spec_freq.findData(p.spec_freq)))
            self.spec_level.setCurrentIndex(max(0, self.spec_level.findData(p.spec_level)))
            self.lp_nom.set_value(p.lp_nom, 4)
            self.lp_tol.setValue(p.lp_tol)
            self.llk_max.set_value(p.llk_max, 4)
            self.llk_pct.setText("" if p.llk_pct_max is None else f"{p.llk_pct_max:g}")
            self.lp_equ.set_value(p.lp_equ)
            self.llk_equ.set_value(p.llk_equ)
            for f, cb in self.freq_boxes.items():
                cb.setChecked(f in p.freqs)
            for lv, cb in self.level_boxes.items():
                cb.setChecked(lv in p.levels)
            self.settle.setValue(p.settle)
            self.navg.setValue(p.navg)
            self.sec_full.setChecked(p.sec_full_matrix)
        finally:
            self._loading = False
        self._profile_changed()

    def _add_secondary(self, w: Winding):
        if self.sec_table.rowCount() >= MAX_SECONDARIES:
            return
        self.sec_table.blockSignals(True)
        r = self.sec_table.rowCount()
        self.sec_table.insertRow(r)
        self.sec_table.setItem(r, 0, QTableWidgetItem(w.name))
        self.sec_table.setItem(r, 1, QTableWidgetItem(w.pins))
        self.sec_table.setItem(r, 2, QTableWidgetItem(fmt(w.dcr_max, "Ω", 4) if w.dcr_max else ""))
        self.sec_table.blockSignals(False)
        self._profile_changed()

    def _remove_secondary(self):
        r = self.sec_table.currentRow()
        if r < 0:
            r = self.sec_table.rowCount() - 1
        if r >= 0:
            self.sec_table.removeRow(r)
            self._profile_changed()

    def _profile_changed(self):
        if self._loading:
            return
        if self.qs is not None:
            self.qs.setValue("flyback/profile", json.dumps(self.profile().to_dict()))
            self.qs.setValue("flyback/operator", self.operator.text())
        self._rebuild_steps()
        self._refresh_results()

    def _load_saved_profile(self):
        p = FlybackProfile()
        if self.qs is not None:
            raw = self.qs.value("flyback/profile", "")
            if raw:
                try:
                    p = FlybackProfile.from_dict(json.loads(raw))
                except (ValueError, TypeError):
                    pass
            self.operator.setText(self.qs.value("flyback/operator", ""))
        self.set_profile(p)

    def _load_profile_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load transformer profile", "", "Profile (*.json)")
        if path:
            try:
                with open(path, encoding="utf-8") as f:
                    self.set_profile(FlybackProfile.from_dict(json.load(f)))
            except (OSError, ValueError, TypeError) as exc:
                QMessageBox.warning(self, "Load failed", str(exc))

    def _save_profile_file(self):
        p = self.profile()
        path, _ = QFileDialog.getSaveFileName(self, "Save transformer profile", f"{p.part_number or 'flyback'}.json",
                                              "Profile (*.json)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(p.to_dict(), f, indent=2)

    # ============================================================ steps ==
    def _rebuild_steps(self):
        self.steps = build_steps(self.profile())
        keys = {s.key for s in self.steps}
        self.results = {k: v for k, v in self.results.items() if k in keys}
        cur = self.step_table.currentRow()
        self.step_table.setRowCount(len(self.steps))
        for i, s in enumerate(self.steps):
            self.step_table.setItem(i, 0, QTableWidgetItem(f"{i + 1}. {s.title}"))
            st = self.status.get(s.key, "pending")
            it = QTableWidgetItem(st.upper())
            kind = STATUS_KIND.get(st, "")
            it.setForeground(QColor(theme.c.get(kind, theme.c["muted"]) if kind else theme.c["muted"]))
            f = it.font()
            f.setBold(True)
            it.setFont(f)
            self.step_table.setItem(i, 1, it)
            self.step_table.setItem(i, 2, QTableWidgetItem(self._step_summary(s.key)))
        self.step_table.setCurrentCell(min(max(cur, 0), len(self.steps) - 1), 0)
        self._show_step()

    def _step_summary(self, key):
        res = self.results.get(key)
        if not res or not res["rows"]:
            return ""
        p = self.profile()
        pt = next((r for r in res["rows"] if r["freq"] == p.spec_freq and r["level"] == p.spec_level), res["rows"][-1])
        text = fmt(pt["L"], "H", 4)
        if res.get("dcr") is not None:
            text += f" · {fmt(res['dcr'], 'Ω', 4)}"
        return text

    def _show_step(self):
        i = self.step_table.currentRow()
        if not (0 <= i < len(self.steps)):
            return
        s = self.steps[i]
        p = self.profile()
        self.diagram.set_state(p.primary.name, [w.name for w in p.secondaries], s.measured, s.shorted)
        n = len(s.freqs) * len(s.levels)
        self.instruction.setText(f"<b>{s.title}</b><br>{s.instruction}<br>"
                                 f"<span style='color:{theme.c['muted']}'>{n} point(s): "
                                 f"{', '.join(hz_label(f) for f in s.freqs)} × {', '.join(v_label(v) for v in s.levels)}"
                                 f"{' + DCR' if s.dcr else ''} · {'series' if s.equ == 'SER' else 'parallel'} model</span>")

    def run_selected(self):
        i = self.step_table.currentRow()
        if not (0 <= i < len(self.steps)) or self.running_key:
            return
        step = self.steps[i]
        p = self.profile()
        self.running_key = step.key
        self.status[step.key] = "running"
        self.results[step.key] = {"step": step.key, "rows": [], "dcr": None, "aborted": False, "equ": step.equ}
        self._set_running(True)
        self._rebuild_steps()
        self.worker.job(lambda m, ctx, s=step: run_step(m, ctx, s, p.settle, p.navg), tag=self.TAG)

    def _set_running(self, on):
        self.run_btn.setEnabled(self.connected and not on)
        self.abort_btn.setEnabled(on)
        self.new_btn.setEnabled(not on)
        if not on:
            self.bar.setValue(0)

    def on_progress(self, tag, i, n, msg):
        if tag == self.TAG:
            self.bar.setMaximum(n)
            self.bar.setValue(i)
            self.progress_text.setText(msg)

    def on_partial(self, tag, data):
        if tag == self.TAG and self.running_key and data.get("step") == self.running_key:
            self.results[self.running_key]["rows"].append(data["row"])
            self._refresh_results()

    def on_done(self, tag, result):
        if tag != self.TAG or not self.running_key:
            return
        key = self.running_key
        self.running_key = None
        self.results[key] = result
        self.status[key] = "aborted" if result.get("aborted") else "done"
        self._set_running(False)
        self.progress_text.setText("Aborted — partial data kept." if result.get("aborted") else "Step complete. "
                                   "Meter settings restored.")
        self._rebuild_steps()
        self._refresh_results()
        if not result.get("aborted"):
            nxt = next((i for i, s in enumerate(self.steps) if self.status.get(s.key) != "done"), None)
            if nxt is not None:
                self.step_table.setCurrentCell(nxt, 0)
                self.progress_text.setText(f"Step complete. Reconnect for step {nxt + 1} and press Run.")
            else:
                self.progress_text.setText("All steps complete — export the report.")

    def on_error(self, tag, msg):
        if tag != self.TAG or not self.running_key:
            return
        self.status[self.running_key] = "error"
        self.results.pop(self.running_key, None)
        self.running_key = None
        self._set_running(False)
        self.progress_text.setText(f"Step failed: {msg}")
        self._rebuild_steps()

    def new_unit(self):
        self.results, self.status = {}, {}
        self.serial.clear()
        self.serial.setFocus()
        self._rebuild_steps()
        self._refresh_results()
        self.step_table.setCurrentCell(0, 0)

    # =========================================================== results ==
    def _refresh_results(self):
        if not hasattr(self, "summary"):
            return
        for plot, key in ((self.lp_plot, "lp"), (self.llk_plot, "llk")):
            item = plot.getPlotItem()
            item.clear()
            if item.legend:
                item.legend.clear()
            res = self.results.get(key)
            if not res:
                continue
            for lv in LEVELS:
                pts = sorted((r["hz"], r["L"]) for r in res["rows"] if r["level"] == lv)
                if pts:
                    col = LEVEL_COLORS[lv]
                    item.plot([a for a, _ in pts], [b for _, b in pts], pen=pg.mkPen(col, width=2), symbol="o",
                              symbolSize=6, symbolBrush=col, symbolPen=None, name=v_label(lv))
        rows, verdict = evaluate(self.profile(), self.results)
        self.summary.setRowCount(len(rows))
        c = theme.c
        for i, r in enumerate(rows):
            for j, text in enumerate([r["param"], r["value"], r["limit"], r["status"] if r["status"] != "INFO" else ""]):
                it = QTableWidgetItem(text)
                it.setToolTip(f"{r['param']} — {r['cond']}")
                if j == 3 and r["status"] in ("PASS", "FAIL"):
                    it.setForeground(QColor(c["good"] if r["status"] == "PASS" else c["bad"]))
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                self.summary.setItem(i, j, it)
        has_data = any(v["rows"] for v in self.results.values())
        self.verdict_badge.setText(verdict if has_data else "NOT TESTED")
        self.verdict_badge.set_kind({"PASS": "good", "FAIL": "bad", "INCOMPLETE": "warn"}.get(verdict, "accent")
                                    if has_data else "")

    # ============================================================ report ==
    def set_connected(self, on, idn: str = ""):
        self.connected = on
        if on and idn:
            parts = idn.split(",")
            self.meta["meter"] = " · ".join(x for x in (parts[1] if len(parts) > 1 else idn,
                                                           f"S/N {parts[2]}" if len(parts) > 2 else "",
                                                           f"FW {parts[3]}" if len(parts) > 3 else "") if x)
        self._set_running(bool(self.running_key))

    def apply_settings(self, st):
        self.meta["speed"] = st.get("speed") or self.meta["speed"]
        if "open_corr" in st:
            self.meta["correction"] = f"Open {'✓' if st.get('open_corr') else '✗'}   Short {'✓' if st.get('short_corr') else '✗'}"
            ok = st.get("open_corr") and st.get("short_corr")
            self.hint.setText("Open/short correction is active on the meter." if ok else
                              "⚠ Open/short correction is not active — run it on the meter with the same leads.")

    def _meta(self):
        return default_meta(serial=self.serial.text().strip(), operator=self.operator.text().strip(),
                            notes=self.notes.text().strip(), app_version=__version__, **self.meta)

    def _default_name(self, ext):
        p = self.profile()
        stem = "_".join(x for x in (p.part_number, self.serial.text().strip()) if x) or "flyback"
        return f"{stem}_{datetime.now():%Y%m%d_%H%M}.{ext}".replace(" ", "_").replace("/", "-")

    def preview(self):
        img = render_image(self.profile(), self.results, self._meta(), dpi=100)
        ReportPreview(img, self.export_pdf, self).exec()

    def export_pdf(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export report", self._default_name("pdf"), "PDF (*.pdf)")
        if path:
            verdict = export_pdf(path, self.profile(), self.results, self._meta())
            self.progress_text.setText(f"Report saved ({verdict}): {path}")

    def export_data(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export raw data", self._default_name("xlsx"),
                                              "Excel workbook (*.xlsx)")
        if not path:
            return
        from openpyxl import Workbook
        from openpyxl.styles import Font
        p = self.profile()
        wb = Workbook()
        ws = wb.active
        ws.title = "Summary"
        meta = self._meta()
        for k in ("date", "serial", "operator", "meter", "correction", "notes"):
            ws.append([k.title(), meta.get(k, "")])
        ws.append(["Part number", p.part_number])
        ws.append([])
        rows, verdict = evaluate(p, self.results)
        ws.append(["Verdict", verdict])
        ws.append(["Parameter", "Condition", "Measured", "Limit", "Result"])
        for c in ws[ws.max_row]:
            c.font = Font(bold=True)
        for r in rows:
            ws.append([r["param"], r["cond"], r["value"], r["limit"], r["status"]])
        names = {"lp": "Lp (open)", "llk": "Llk (shorted)"}
        names.update({f"sec{i}": w.name[:28] for i, w in enumerate(p.secondaries)})
        for key, res in self.results.items():
            sh = wb.create_sheet(names.get(key, key)[:31])
            sh.append(["Level", "Frequency (Hz)", "L (H)", "L σ (H)", "Q", "Model"])
            for c in sh[1]:
                c.font = Font(bold=True)
            for r in res["rows"]:
                sh.append([r["level"], r["hz"], r["L"], r["L_sd"], r["Q"], res.get("equ")])
            if res.get("dcr") is not None:
                sh.append([])
                sh.append(["DCR (Ω)", res["dcr"]])
        wb.save(path)
        self.progress_text.setText(f"Data saved: {path}")
