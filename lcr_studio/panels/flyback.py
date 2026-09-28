"""Flyback transformer test: Lp / Llk measurements and a one-page report."""
from __future__ import annotations

import json
import math
import platform
import time
from datetime import datetime

import pyqtgraph as pg
from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QGridLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea,
                               QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import __version__
from ..engmath import fmt
from ..flyback import FlybackProfile, build_steps, evaluate, hz_label, run_step, v_label
from ..report import LEVEL_COLORS, default_meta, export_pdf, render_image
from ..theme import theme
from ..ut622e import FREQ_HZ, FREQUENCIES, LEVELS
from ..widgets import (Badge, BusyDialog, Card, Segmented, SmoothRange, UnitEdit, field_label, make_plot, muted,
                       plot_series, range_with_limits, reveal_fraction, watch_manual_zoom)

STATUS_KIND = {"done": "good", "running": "accent", "aborted": "warn", "error": "bad", "pending": ""}


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
        self.busy = None                      # pop-up shown while a step runs
        self._anim_t0 = None
        self._anim = QTimer(self)             # redraws while the newest point grows in
        self._anim.setInterval(16)
        self._anim.timeout.connect(self._refresh_results)

        root = QHBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)
        root.addWidget(self._build_setup())
        split = QSplitter(Qt.Vertical)
        split.setChildrenCollapsible(False)
        split.addWidget(self._build_steps())
        split.addWidget(self._build_results())
        split.setSizes([250, 600])
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
        self.setup_scroll = scroll
        col = QVBoxLayout(inner)
        col.setContentsMargins(0, 0, 8, 0)
        col.setSpacing(10)

        unit = Card("Unit under test")
        g = QGridLayout()
        g.setVerticalSpacing(6)
        self.part = QLineEdit()
        self.part.setPlaceholderText("e.g. FBT-EE25-12V")
        self.station = QLineEdit(platform.node())
        self.station.setReadOnly(True)
        self.station.setToolTip("This computer's name — printed on the report as the test station")
        self.notes = QLineEdit()
        self.notes.setPlaceholderText("printed on the report")
        for i, (lab, wdg) in enumerate([("Part number", self.part), ("Station", self.station),
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

        spec = Card("Specification")
        g = QGridLayout()
        g.setVerticalSpacing(6)
        self.spec_freq = QComboBox()
        for f in FREQUENCIES:
            self.spec_freq.addItem(hz_label(f), f)
        self.spec_level = QComboBox()
        for lv in LEVELS:
            self.spec_level.addItem(v_label(lv), lv)
        self.lp_nom = UnitEdit("H", required=True)
        self.lp_tol = QDoubleSpinBox()
        self.lp_tol.setRange(0.1, 50)
        self.lp_tol.setSuffix(" %")
        self.lp_tol.setValue(10)
        self.llk_max = UnitEdit("H", required=True)
        self.llk_pct = UnitEdit("%", prefixes=("",), required=True)
        self.lp_equ = Segmented([("SER", "Series"), ("PAR", "Parallel")])
        self.llk_equ = Segmented([("SER", "Series"), ("PAR", "Parallel")])
        rows = [("Test frequency", self.spec_freq), ("Test level", self.spec_level), ("Lp nominal", self.lp_nom),
                ("Lp tolerance ±", self.lp_tol), ("Llk max", self.llk_max), ("Llk / Lp max (%)", self.llk_pct),
                ("Lp circuit model", self.lp_equ), ("Llk circuit model", self.llk_equ)]
        for i, (lab, wdg) in enumerate(rows):
            g.addWidget(field_label(lab), i, 0)
            g.addWidget(wdg, i, 1)
        spec.body.addLayout(g)
        self.spec_hint = muted("")
        spec.body.addWidget(self.spec_hint)
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
        col.addWidget(mat)
        col.addStretch(1)

        self.part.textChanged.connect(lambda _t: self._profile_changed())
        for w in (self.lp_nom, self.llk_max, self.llk_pct):
            w.valueChanged.connect(lambda _v: self._profile_changed())
        for w in (self.spec_freq, self.spec_level):
            w.currentIndexChanged.connect(lambda _i: self._profile_changed())
        for w in (self.lp_tol,):
            w.valueChanged.connect(lambda _v: self._profile_changed())
        for w in (self.settle, self.navg):
            w.valueChanged.connect(lambda _v: self._profile_changed())
        for seg in (self.lp_equ, self.llk_equ):
            seg.changed.connect(lambda _v: self._profile_changed())
        for cb in list(self.freq_boxes.values()) + list(self.level_boxes.values()):
            cb.toggled.connect(lambda _on: self._profile_changed())
        return scroll

    # ============================================================ steps ==
    def _build_steps(self):
        card = Card("Run test")
        row = QHBoxLayout()
        row.addWidget(field_label("Step"))
        self.step_pick = QComboBox()
        self.step_pick.setToolTip("Measurement to run")
        self.step_pick.currentIndexChanged.connect(lambda _i: self._show_step())
        row.addWidget(self.step_pick, 1)
        self.run_btn = QPushButton("Run step")
        self.run_btn.setObjectName("Accent")
        self.run_btn.clicked.connect(self.run_selected)
        self.abort_btn = QPushButton("Abort")
        self.abort_btn.setEnabled(False)
        self.abort_btn.clicked.connect(self.worker.abort_job)
        self.new_btn = QPushButton("New unit")
        self.new_btn.setToolTip("Clear results for the next transformer (keeps the profile)")
        self.new_btn.clicked.connect(self.new_unit)
        row.addWidget(self.run_btn)
        row.addWidget(self.abort_btn)
        row.addWidget(self.new_btn)
        card.body.addLayout(row)
        self.instruction = QLabel("")
        self.instruction.setWordWrap(True)
        self.instruction.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        card.body.addWidget(self.instruction)
        self.progress_text = muted("")
        card.body.addWidget(self.progress_text)
        self.step_status = QLabel("")
        self.step_status.setWordWrap(True)
        card.body.addWidget(self.step_status)
        self.hint = muted("Run open/short correction on the meter with the same leads before testing.")
        card.body.addWidget(self.hint)
        return card

    # ========================================================== results ==
    def _build_results(self):
        card = Card("Results")
        self.verdict_badge = Badge("NOT TESTED")
        card.header.addWidget(self.verdict_badge)
        prev = QPushButton("Preview report")
        prev.clicked.connect(self.preview)
        pdf = QPushButton("Export PDF…")
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
        # specification limits: Lp nominal ± tolerance as a band, Llk max as a line
        self.lp_band = pg.LinearRegionItem(orientation="horizontal", movable=False)
        self.lp_band.setZValue(-10)
        self.lp_nom_line = pg.InfiniteLine(angle=0, movable=False, label="nominal", labelOpts={"position": 0.8})
        self.llk_line = pg.InfiniteLine(angle=0, movable=False, label="Llk max", labelOpts={"position": 0.8})
        self._restyle_limits(theme.c)
        theme.changed.connect(self._restyle_limits)
        self.ease = {"lp": SmoothRange(self.lp_plot, padding=0), "llk": SmoothRange(self.llk_plot, padding=0)}
        self._follow = True
        watch_manual_zoom(self, self.lp_plot, self.llk_plot)
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
        return FlybackProfile(
            part_number=self.part.text().strip(),
            spec_freq=self.spec_freq.currentData(), spec_level=self.spec_level.currentData(),
            lp_nom=self.lp_nom.value(), lp_tol=self.lp_tol.value(), llk_max=self.llk_max.value(),
            llk_pct_max=self.llk_pct.value(), lp_equ=self.lp_equ.value() or "SER", llk_equ=self.llk_equ.value() or "SER",
            freqs=[f for f, cb in self.freq_boxes.items() if cb.isChecked()],
            levels=[lv for lv, cb in self.level_boxes.items() if cb.isChecked()],
            settle=self.settle.value(), navg=self.navg.value())

    def set_profile(self, p: FlybackProfile):
        self._loading = True
        try:
            self.part.setText(p.part_number)
            self.spec_freq.setCurrentIndex(max(0, self.spec_freq.findData(p.spec_freq)))
            self.spec_level.setCurrentIndex(max(0, self.spec_level.findData(p.spec_level)))
            self.lp_nom.set_value(p.lp_nom, 4)
            self.lp_tol.setValue(p.lp_tol)
            self.llk_max.set_value(p.llk_max, 4)
            self.llk_pct.set_value(p.llk_pct_max)
            self.lp_equ.set_value(p.lp_equ)
            self.llk_equ.set_value(p.llk_equ)
            for f, cb in self.freq_boxes.items():
                cb.setChecked(f in p.freqs)
            for lv, cb in self.level_boxes.items():
                cb.setChecked(lv in p.levels)
            self.settle.setValue(p.settle)
            self.navg.setValue(p.navg)
        finally:
            self._loading = False
        self._profile_changed()

    def _profile_changed(self):
        if self._loading:
            return
        if self.qs is not None:
            self.qs.setValue("flyback/profile", json.dumps(self.profile().to_dict()))
        self._rebuild_steps()
        self._results_changed()
        self._update_run_enabled()

    def _load_saved_profile(self):
        p = FlybackProfile()
        if self.qs is not None:
            raw = self.qs.value("flyback/profile", "")
            if raw:
                try:
                    p = FlybackProfile.from_dict(json.loads(raw))
                except (ValueError, TypeError):
                    pass
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
        cur = max(self.step_pick.currentIndex(), 0)
        self.step_pick.blockSignals(True)
        self.step_pick.clear()
        for i, s in enumerate(self.steps):
            self.step_pick.addItem(f"{i + 1}. {s.title}", s.key)
        self.step_pick.setCurrentIndex(min(cur, len(self.steps) - 1))
        self.step_pick.blockSignals(False)
        lines = []
        for i, s in enumerate(self.steps):
            st = self.status.get(s.key, "pending")
            kind = STATUS_KIND.get(st, "")
            color = theme.c.get(kind, theme.c["muted"]) if kind else theme.c["muted"]
            summary = self._step_summary(s.key)
            lines.append(f"{i + 1}. {s.title}: <b style='color:{color}'>{st.upper()}</b>"
                         + (f" · {summary}" if summary else ""))
        self.step_status.setText("<br>".join(lines))
        self._show_step()

    def _step_summary(self, key):
        res = self.results.get(key)
        if not res or not res["rows"]:
            return ""
        p = self.profile()
        pt = next((r for r in res["rows"] if r["freq"] == p.spec_freq and r["level"] == p.spec_level), res["rows"][-1])
        text = fmt(pt["L"], "H", 4)
        return text

    def _show_step(self):
        i = self.step_pick.currentIndex()
        if not (0 <= i < len(self.steps)):
            return
        s = self.steps[i]
        n = len(s.freqs) * len(s.levels)
        self.instruction.setText(f"<b>{s.title}</b><br>{s.instruction}<br>"
                                 f"<span style='color:{theme.c['muted']}'>{n} point(s): "
                                 f"{', '.join(hz_label(f) for f in s.freqs)} × {', '.join(v_label(v) for v in s.levels)}"
                                 f" · {'series' if s.equ == 'SER' else 'parallel'} model</span>")

    def _spec_complete(self) -> bool:
        """Every limit in Specification holds a positive value."""
        return all(v is not None and v > 0 for v in (self.lp_nom.value(), self.llk_max.value(),
                                                     self.llk_pct.value()))

    def _update_run_enabled(self):
        self.run_btn.setEnabled(self.connected and not self.running_key)
        if self._spec_complete():
            self.spec_hint.setText("Limits are checked at the test frequency and level.")
            self.spec_hint.setStyleSheet("")
        else:
            self.spec_hint.setText("Fill in every limit to run a step.")

    def _flag_missing(self):
        """Run step was pressed with limits missing: say so and jump to the first empty one."""
        self.spec_hint.setText("⚠ Fill in every limit before running a step.")
        self.spec_hint.setStyleSheet(f"color: {theme.c['bad']};")
        self.progress_text.setText("Fill in every limit in Specification first.")
        for w in (self.lp_nom, self.llk_max, self.llk_pct):
            if w.value() is None or w.value() <= 0:
                self.setup_scroll.ensureWidgetVisible(w)
                w.setFocus()
                break

    def run_selected(self):
        i = self.step_pick.currentIndex()
        if not (0 <= i < len(self.steps)) or self.running_key:
            return
        if not self._spec_complete():
            self._flag_missing()
            return
        step = self.steps[i]
        p = self.profile()
        self.running_key = step.key
        self.status[step.key] = "running"
        self.results[step.key] = {"step": step.key, "rows": [], "aborted": False, "equ": step.equ}
        self._set_running(True)
        self._rebuild_steps()
        self.worker.job(lambda m, ctx, s=step: run_step(m, ctx, s, p.settle, p.navg), tag=self.TAG)
        self.busy = BusyDialog("Measuring", step.title, self.worker.abort_job, self)
        self.busy.open_centered()

    def _set_running(self, on):
        self.abort_btn.setEnabled(on)
        self.new_btn.setEnabled(not on)
        self._update_run_enabled()

    def on_progress(self, tag, i, n, msg):
        if tag == self.TAG:
            self.progress_text.setText(msg)
            if self.busy is not None:
                detail = msg.split(" · ", 1)[1] if " · " in msg else msg
                self.busy.progress(i, n, f"{detail}   ·   {i} / {n}")

    def on_partial(self, tag, data):
        if tag == self.TAG and self.running_key and data.get("step") == self.running_key:
            self.results[self.running_key]["rows"].append(data["row"])
            self._anim_t0 = time.monotonic()
            self._anim.start()
            self._results_changed()

    def on_done(self, tag, result):
        if tag != self.TAG or not self.running_key:
            return
        key = self.running_key
        self.running_key = None
        self.results[key] = result
        self.status[key] = "aborted" if result.get("aborted") else "done"
        self._set_running(False)
        self.progress_text.setText("Aborted. Partial data kept." if result.get("aborted") else "Step complete.")
        self._rebuild_steps()
        self._results_changed()
        if not result.get("aborted"):
            nxt = next((i for i, s in enumerate(self.steps) if self.status.get(s.key) != "done"), None)
            if nxt is not None:
                self.step_pick.setCurrentIndex(nxt)
                self.progress_text.setText(f"Step complete. Wire up for step {nxt + 1} and press Run.")
            else:
                self.progress_text.setText("All steps complete. Export the report.")
        if self.busy is not None:
            self.busy.finish(self.progress_text.text(), ok=not result.get("aborted"))
            self.busy = None

    def on_error(self, tag, msg):
        if tag != self.TAG or not self.running_key:
            return
        self.status[self.running_key] = "error"
        self.results.pop(self.running_key, None)
        self.running_key = None
        self._set_running(False)
        self.progress_text.setText(f"Step failed: {msg}")
        self._rebuild_steps()
        if self.busy is not None:
            self.busy.finish(f"Failed: {msg}", ok=False, delay_ms=2500)
            self.busy = None

    def new_unit(self):
        self.results, self.status = {}, {}
        self._rebuild_steps()
        self._results_changed()
        self.step_pick.setCurrentIndex(0)

    # =========================================================== results ==
    def _restyle_limits(self, c):
        self.lp_band.setBrush(pg.mkBrush(c["good"] + "22"))
        for line in self.lp_band.lines:
            line.setPen(pg.mkPen(c["good"], width=1, style=Qt.DashLine))
        self.lp_nom_line.setPen(pg.mkPen(c["muted"], width=1, style=Qt.DotLine))
        self.lp_nom_line.label.setColor(c["muted"])
        self.llk_line.setPen(pg.mkPen(c["bad"], width=1, style=Qt.DashLine))
        self.llk_line.label.setColor(c["bad"])

    def _place_limits(self, item, key, p):
        """Re-add the limit band / line after a clear; returns the limit values shown, for the y range."""
        if key == "lp":
            item.addItem(self.lp_band)
            item.addItem(self.lp_nom_line)
            on = bool(p.lp_nom)
            self.lp_band.setVisible(on)
            self.lp_nom_line.setVisible(on)
            if not on:
                return []
            lo, hi = p.lp_nom * (1 - p.lp_tol / 100), p.lp_nom * (1 + p.lp_tol / 100)
            self.lp_band.setRegion((lo, hi))
            self.lp_nom_line.setPos(p.lp_nom)
            return [lo, hi]
        item.addItem(self.llk_line)
        self.llk_line.setVisible(bool(p.llk_max))
        if not p.llk_max:
            return []
        self.llk_line.setPos(p.llk_max)
        return [p.llk_max]

    def _results_changed(self):
        self._follow = True                      # new data or limits: follow again after a manual zoom
        self._refresh_results()

    def _refresh_results(self):
        if not hasattr(self, "summary"):
            return
        reveal = 1.0
        if self._anim_t0 is not None:
            reveal, done = reveal_fraction(self._anim_t0)
            if done:
                self._anim_t0 = None
                self._anim.stop()
        live = self.results.get(self.running_key, {}).get("rows") if self.running_key else None
        newest = live[-1] if live else None
        p = self.profile()
        for plot, key in ((self.lp_plot, "lp"), (self.llk_plot, "llk")):
            item = plot.getPlotItem()
            item.clear()
            if item.legend:
                item.legend.clear()
            limits = self._place_limits(item, key, p)
            res = self.results.get(key)
            ys = [r["L"] for r in res["rows"]] if res else []
            for lv in (LEVELS if res else ()):
                lv_rows = sorted((r for r in res["rows"] if r["level"] == lv), key=lambda r: r["hz"])
                if lv_rows:
                    rv = reveal if lv_rows[-1] is newest else 1.0
                    plot_series(plot, [r["hz"] for r in lv_rows], [r["L"] for r in lv_rows], LEVEL_COLORS[lv],
                                v_label(lv), log_x=True, symbol_size=6, reveal=rv)
            if ys or limits:                     # fit the curves; a limit joins the view once it is near
                lo, hi = range_with_limits(ys, limits) if ys else (min(limits), max(limits))
                pad = (hi - lo) * 0.12 or abs(hi) * 0.05 or 1e-9
                self.ease[key].set_target(lo - pad, hi + pad)
        moving = False
        if self._follow:
            for e in self.ease.values():
                moving = e.tick() or moving
        if moving or self._anim_t0 is not None:     # keep the display timer running until everything settles
            if not self._anim.isActive():
                self._anim.start()
        else:
            self._anim.stop()
        rows, verdict = evaluate(p, self.results)
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
                              "⚠ Open/short correction is off. Run it on the meter with the same leads.")

    def _meta(self):
        return default_meta(station=self.station.text().strip(), notes=self.notes.text().strip(),
                            app_version=__version__, **self.meta)

    def _default_name(self, ext):
        p = self.profile()
        stem = p.part_number or "flyback"
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
        for k in ("date", "station", "meter", "correction", "notes"):
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
        for key, res in self.results.items():
            sh = wb.create_sheet(names.get(key, key)[:31])
            sh.append(["Level", "Frequency (Hz)", "L (H)", "L σ (H)", "Q", "Model"])
            for c in sh[1]:
                c.font = Font(bold=True)
            for r in res["rows"]:
                sh.append([r["level"], r["hz"], r["L"], r["L_sd"], r["Q"], res.get("equ")])
        wb.save(path)
        self.progress_text.setText(f"Data saved: {path}")
