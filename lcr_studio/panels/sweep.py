"""Frequency / level sweep with multi-parameter capture and run overlays."""
from __future__ import annotations

import math
import statistics
import time

import pyqtgraph as pg
import pyqtgraph.exporters  # noqa: F401  (registers ImageExporter)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QGridLayout, QHBoxLayout,
                               QLineEdit, QListWidget, QListWidgetItem, QProgressBar, QPushButton,
                               QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..engmath import fmt
from ..theme import SERIES, theme
from ..ut622e import FREQ_HZ, FREQUENCIES, LEVELS, PRIMARY_UNIT, SECONDARY_LABEL, SECONDARY_UNIT, MeterError
from ..widgets import (Card, SmoothRange, export_table, field_label, make_plot, muted, plot_series, reveal_fraction,
                       watch_manual_zoom)

SWEEP_SECONDARIES = [("D", "D"), ("Q", "Q"), ("ESR", "ESR"), ("X", "X"), ("DEG", "θ°")]
PERIOD = {"SLOW": 0.5, "MED": 0.2, "FAST": 0.05}


class _Aborted(Exception):
    pass


def sweep_job(meter, ctx, freqs, levels, secs, settle, navg):
    st0 = meter.read_settings(full=False)
    ptype = st0.get("primary")
    if ptype == "DCR":
        raise ValueError("Sweeps need an AC function (L, C, R or Z), not DCR.")
    get = meter.fetch if st0.get("trigger") == "AUTO" else meter.trigger_fetch
    rows, aborted = [], False
    total = len(levels) * len(freqs) * max(1, len(secs))
    step = 0
    try:
        for lv in levels:
            meter.set_level(lv)
            for f in freqs:
                meter.set_frequency(f)
                prim, secvals = [], {}
                for sname in secs or [None]:
                    if sname:
                        meter.set_secondary(sname)
                    for _ in range(settle + 1):       # +1: first result may predate the change
                        if ctx.aborted:
                            raise _Aborted
                        get()
                    vals = []
                    for _ in range(navg):
                        if ctx.aborted:
                            raise _Aborted
                        vals.append(get())
                    prim += [v[0] for v in vals]
                    if sname:
                        secvals[sname] = statistics.fmean(v[1] for v in vals)
                    step += 1
                    ctx.progress(step, total, f"{lv} · {f}" + (f" · {SECONDARY_LABEL[sname]}" if sname else ""))
                row = {"level": lv, "freq": f, "hz": FREQ_HZ[f], "p": statistics.fmean(prim),
                       "p_sd": statistics.stdev(prim) if len(prim) > 1 else 0.0, **secvals}
                rows.append(row)
                ctx.partial(row)
    except _Aborted:
        aborted = True
    finally:
        for fn, key in ((meter.set_level, "level"), (meter.set_frequency, "frequency"),
                        (meter.set_secondary, "secondary")):
            if st0.get(key):
                try:
                    fn(st0[key])
                except MeterError:
                    pass
    return {"ptype": ptype, "equ": st0.get("equivalent"), "rows": rows, "aborted": aborted}


class SweepPanel(QWidget):
    def __init__(self, worker, parent=None):
        super().__init__(parent)
        self.worker = worker
        self.runs = []
        self.running = None
        self.speed = "MED"
        self.connected = False
        self._anim_t0 = None
        self._anim = QTimer(self)             # redraws while the newest point grows in
        self._anim.setInterval(16)
        self._anim.timeout.connect(self._redraw)

        root = QHBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # ---------------- configuration column
        left = QVBoxLayout()
        left.setSpacing(10)
        cfg = Card("Sweep setup")
        cfg.body.addWidget(field_label("Frequencies"))
        self.freq_boxes = {}
        g = QGridLayout()
        for i, f in enumerate(FREQUENCIES):
            cb = QCheckBox(f)
            cb.setChecked(True)
            cb.toggled.connect(self._update_estimate)
            self.freq_boxes[f] = cb
            g.addWidget(cb, i // 3, i % 3)
        cfg.body.addLayout(g)
        cfg.body.addWidget(field_label("Test levels"))
        self.level_boxes = {}
        row = QHBoxLayout()
        for lv in LEVELS:
            cb = QCheckBox(lv)
            cb.setChecked(lv == "0.3V")
            cb.toggled.connect(self._update_estimate)
            self.level_boxes[lv] = cb
            row.addWidget(cb)
        cfg.body.addLayout(row)
        cfg.body.addWidget(field_label("Also record"))
        self.sec_boxes = {}
        g = QGridLayout()
        for i, (s, label) in enumerate(SWEEP_SECONDARIES):
            cb = QCheckBox(label)
            cb.setChecked(s in ("D", "ESR"))
            cb.toggled.connect(self._update_estimate)
            self.sec_boxes[s] = cb
            g.addWidget(cb, i // 3, i % 3)
        cfg.body.addLayout(g)
        g = QGridLayout()
        self.settle = QSpinBox()
        self.settle.setRange(0, 20)
        self.settle.setValue(2)
        self.settle.setToolTip("Readings to discard after each change")
        self.navg = QSpinBox()
        self.navg.setRange(1, 100)
        self.navg.setValue(5)
        self.navg.setToolTip("Readings averaged per point")
        for w in (self.settle, self.navg):
            w.valueChanged.connect(self._update_estimate)
        g.addWidget(field_label("Settle (discard)"), 0, 0)
        g.addWidget(self.settle, 0, 1)
        g.addWidget(field_label("Average"), 1, 0)
        g.addWidget(self.navg, 1, 1)
        g.addWidget(field_label("Run name"), 2, 0)
        self.name = QLineEdit()
        self.name.setPlaceholderText("e.g. C12 100µF")
        g.addWidget(self.name, 2, 1)
        cfg.body.addLayout(g)
        self.estimate = muted("")
        cfg.body.addWidget(self.estimate)
        row = QHBoxLayout()
        self.run_btn = QPushButton("Run sweep")
        self.run_btn.setObjectName("Accent")
        self.run_btn.clicked.connect(self.start)
        self.abort_btn = QPushButton("Abort")
        self.abort_btn.setEnabled(False)
        self.abort_btn.clicked.connect(self.worker.abort_job)
        row.addWidget(self.run_btn, 1)
        row.addWidget(self.abort_btn)
        cfg.body.addLayout(row)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        cfg.body.addWidget(self.bar)
        self.status = muted("Uses the meter's current parameter and circuit model.")
        cfg.body.addWidget(self.status)
        left.addWidget(cfg)

        runs = Card("Runs")
        self.run_list = QListWidget()
        self.run_list.itemChanged.connect(lambda _: self._data_changed())
        self.run_list.currentRowChanged.connect(lambda _: self._fill_table())
        runs.body.addWidget(self.run_list, 1)
        row = QHBoxLayout()
        rm = QPushButton("Delete")
        rm.clicked.connect(self._delete_run)
        exp = QPushButton("Export…")
        exp.clicked.connect(self._export)
        img = QPushButton("Save chart…")
        img.clicked.connect(self._save_image)
        row.addWidget(rm)
        row.addWidget(exp)
        row.addWidget(img)
        runs.body.addLayout(row)
        left.addWidget(runs, 1)
        lw = QWidget()
        lw.setLayout(left)
        lw.setFixedWidth(330)
        left.setContentsMargins(0, 0, 0, 0)
        root.addWidget(lw)

        # ---------------- results
        split = QSplitter(Qt.Vertical)
        split.setChildrenCollapsible(False)
        charts = Card("Frequency response")
        self.sec_pick = QComboBox()
        self.sec_pick.currentIndexChanged.connect(lambda _: self._data_changed())
        charts.header.addWidget(field_label("Lower chart"))
        charts.header.addWidget(self.sec_pick)
        self.pplot = make_plot("Primary", "", "Frequency", "Hz", log_x=True)
        self.splot = make_plot("Secondary", "", "Frequency", "Hz", log_x=True)
        self.splot.setXLink(self.pplot)
        ticks = [[(math.log10(FREQ_HZ[f]), f.replace("Hz", "")) for f in FREQUENCIES if f != "120Hz"],
                 [(math.log10(120), "")]]
        for plot in (self.pplot, self.splot):
            ax = plot.getPlotItem().getAxis("bottom")
            ax.enableAutoSIPrefix(False)
            ax.setTicks(ticks)
            plot.getPlotItem().setLabel("bottom", "Frequency (Hz)")
            plot.getPlotItem().setXRange(math.log10(90), math.log10(110e3), padding=0.02)
        self.legend = self.pplot.addLegend(offset=(-10, 10))
        self.ease_p, self.ease_s = SmoothRange(self.pplot), SmoothRange(self.splot)
        self._follow = True
        watch_manual_zoom(self, self.pplot, self.splot)
        charts.body.addWidget(self.pplot, 3)
        charts.body.addWidget(self.splot, 2)
        split.addWidget(charts)
        tcard = Card("Data")
        self.table = QTableWidget()
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        tcard.body.addWidget(self.table)
        split.addWidget(tcard)
        split.setSizes([520, 220])
        root.addWidget(split, 1)
        self.set_connected(False)
        self._update_estimate()

    # ------------------------------------------------------------------
    def set_connected(self, on):
        self.connected = on
        self.run_btn.setEnabled(on and self.running is None)

    def apply_settings(self, st):
        self.speed = st.get("speed") or self.speed
        self._update_estimate()

    def _selection(self):
        freqs = [f for f, cb in self.freq_boxes.items() if cb.isChecked()]
        levels = [lv for lv, cb in self.level_boxes.items() if cb.isChecked()]
        secs = [s for s, cb in self.sec_boxes.items() if cb.isChecked()]
        return freqs, levels, secs

    def _update_estimate(self):
        freqs, levels, secs = self._selection()
        n = len(freqs) * len(levels) * max(1, len(secs))
        # The meter measures more slowly at 100/120 Hz (several signal periods per reading).
        per = PERIOD.get(self.speed, 0.2)
        weights = sum(2.5 if FREQ_HZ[f] < 1000 else 1.0 for f in freqs) * len(levels) * max(1, len(secs))
        secs_t = weights * (self.settle.value() + 1 + self.navg.value()) * (per + 0.02) + n * 0.1
        self.estimate.setText(f"{len(freqs) * len(levels)} points · ≈ {secs_t:.0f} s at {self.speed} speed")

    def start(self):
        freqs, levels, secs = self._selection()
        if not freqs or not levels:
            self.status.setText("Select at least one frequency and one level.")
            return
        name = self.name.text().strip() or f"Run {len(self.runs) + 1}"
        self.running = {"name": name, "rows": [], "secs": secs}
        self.run_btn.setEnabled(False)
        self.abort_btn.setEnabled(True)
        self.bar.setValue(0)
        self.status.setText("Sweeping…")
        self.worker.job(lambda m, ctx: sweep_job(m, ctx, freqs, levels, secs, self.settle.value(),
                                                  self.navg.value()), tag="sweep")

    def on_progress(self, tag, i, n, msg):
        if tag == "sweep":
            self.bar.setMaximum(n)
            self.bar.setValue(i)
            self.status.setText(f"{i}/{n} · {msg}")

    def on_partial(self, tag, row):
        if tag == "sweep" and self.running is not None:
            self.running["rows"].append(row)
            self._anim_t0 = time.monotonic()
            self._anim.start()
            self._data_changed()

    def on_done(self, tag, result):
        if tag != "sweep" or self.running is None:
            return
        run = self.running
        self.running = None
        run.update(ptype=result["ptype"], equ=result["equ"], rows=result["rows"])
        self.abort_btn.setEnabled(False)
        self.run_btn.setEnabled(self.connected)
        self.status.setText("Aborted. Partial data kept." if result["aborted"] else "Sweep complete.")
        if run["rows"]:
            self._add_run(run)

    def on_error(self, tag, msg):
        if tag == "sweep":
            self.running = None
            self.abort_btn.setEnabled(False)
            self.run_btn.setEnabled(self.connected)
            self.status.setText(f"Sweep failed: {msg}")

    # ------------------------------------------------------------------
    def _add_run(self, run):
        run["color"] = SERIES[len(self.runs) % len(SERIES)]
        self.runs.append(run)
        item = QListWidgetItem(f"{run['name']}  ·  {run['ptype']} {'series' if run['equ'] == 'SER' else 'parallel'}")
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked)
        item.setForeground(pg.mkColor(run["color"]))
        self.run_list.addItem(item)
        self.run_list.setCurrentRow(self.run_list.count() - 1)
        self._refresh_sec_pick()
        self._data_changed()

    def _delete_run(self):
        row = self.run_list.currentRow()
        if row >= 0:
            self.runs.pop(row)
            self.run_list.takeItem(row)
            self._refresh_sec_pick()
            self._data_changed()
            self._fill_table()

    def _refresh_sec_pick(self):
        cur = self.sec_pick.currentData()
        self.sec_pick.blockSignals(True)
        self.sec_pick.clear()
        seen = []
        for run in self.runs:
            for s in run["secs"]:
                if s not in seen:
                    seen.append(s)
        for s in seen:
            self.sec_pick.addItem(dict(SWEEP_SECONDARIES)[s], s)
        i = self.sec_pick.findData(cur)
        self.sec_pick.setCurrentIndex(max(i, 0))
        self.sec_pick.blockSignals(False)

    def _visible_runs(self):
        runs = [r for i, r in enumerate(self.runs) if self.run_list.item(i).checkState() == Qt.Checked]
        if self.running and self.running["rows"]:
            live = dict(self.running, ptype=self.worker.state.get("primary"), color=theme.c["text"],
                        name=self.running["name"] + " (running)", live=True)
            runs.append(live)
        return runs

    def _data_changed(self):
        self._follow = True                      # new data: follow it again even after a manual zoom
        self._redraw()

    def _redraw(self):
        reveal = 1.0
        if self._anim_t0 is not None:
            reveal, done = reveal_fraction(self._anim_t0)
            if done:
                self._anim_t0 = None
                self._anim.stop()
        self.pplot.clear()
        self.splot.clear()
        self.legend.clear()
        sec = self.sec_pick.currentData()
        runs = self._visible_runs()
        ptypes = {r["ptype"] for r in runs if r["ptype"]}
        if len(ptypes) == 1:
            pt = ptypes.pop()
            self.pplot.setLabel("left", pt, units=PRIMARY_UNIT.get(pt, ""))
        else:
            self.pplot.setLabel("left", "Primary", units="")
        if sec:
            unit = SECONDARY_UNIT[sec] if sec != "DEG" else ""
            self.splot.getPlotItem().getAxis("left").enableAutoSIPrefix(bool(unit))
            self.splot.setLabel("left", SECONDARY_LABEL[sec] + (" (°)" if sec == "DEG" else ""), units=unit)
        styles = [Qt.SolidLine, Qt.DashLine, Qt.DotLine]
        for run in runs:
            levels = sorted({r["level"] for r in run["rows"]}, key=LEVELS.index)
            for li, lv in enumerate(levels):
                rows = [r for r in run["rows"] if r["level"] == lv]
                x = [r["hz"] for r in rows]
                name = run["name"] + (f" @ {lv}" if len(levels) > 1 else "")
                style = styles[li % 3]
                rv = reveal if run.get("live") and rows[-1] is run["rows"][-1] else 1.0
                plot_series(self.pplot, x, [r["p"] for r in rows], run["color"], name, log_x=True, style=style,
                            reveal=rv)
                if sec and all(sec in r for r in rows):
                    plot_series(self.splot, x, [r[sec] for r in rows], run["color"], log_x=True, style=style,
                                reveal=rv)
        ys = [r["p"] for run in runs for r in run["rows"]]
        if ys:
            self.ease_p.set_target(min(ys), max(ys))
        if sec:
            ss = [r[sec] for run in runs for r in run["rows"] if r.get(sec) is not None]
            if ss:
                self.ease_s.set_target(min(ss), max(ss))
        moving = False
        if self._follow:
            moving = self.ease_p.tick()
            moving = self.ease_s.tick() or moving
        if moving or self._anim_t0 is not None:     # keep the display timer running until everything settles
            if not self._anim.isActive():
                self._anim.start()
        else:
            self._anim.stop()

    def _run_table(self, run):
        secs = run["secs"]
        unit = PRIMARY_UNIT.get(run["ptype"], "")
        headers = ["Level", "Frequency (Hz)", f"{run['ptype']} ({unit})", "σ"] + \
                  [f"{SECONDARY_LABEL[s]}{' (' + SECONDARY_UNIT[s] + ')' if SECONDARY_UNIT[s] else ''}" for s in secs]
        rows = [[r["level"], r["hz"], r["p"], r["p_sd"]] + [r.get(s) for s in secs] for r in run["rows"]]
        return headers, rows

    def _fill_table(self):
        row = self.run_list.currentRow()
        self.table.clear()
        if row < 0 or row >= len(self.runs):
            self.table.setRowCount(0)
            self.table.setColumnCount(0)
            return
        run = self.runs[row]
        headers, rows = self._run_table(run)
        unit = PRIMARY_UNIT.get(run["ptype"], "")
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            cells = [r[0], fmt(r[1], "Hz", 3), fmt(r[2], unit), fmt(r[3], unit, 3)] + \
                    [("—" if v is None else f"{v:.5g}") for v in r[4:]]
            for j, text in enumerate(cells):
                it = QTableWidgetItem(text)
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(i, j, it)
        self.table.resizeColumnsToContents()

    def _export(self):
        if not self.runs:
            return
        all_secs = []
        for run in self.runs:
            all_secs += [s for s in run["secs"] if s not in all_secs]
        headers = ["Run", "Primary", "Model", "Level", "Frequency (Hz)", "Primary value", "Primary σ"] + \
                  [SECONDARY_LABEL[s] + (" (°)" if s == "DEG" else "") for s in all_secs]
        rows = []
        for run in self.runs:
            for r in run["rows"]:
                rows.append([run["name"], run["ptype"], run["equ"], r["level"], r["hz"], r["p"], r["p_sd"]] +
                            [r.get(s) for s in all_secs])
        export_table(self, headers, rows, "sweep.xlsx")

    def _save_image(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save chart", "sweep.png", "PNG image (*.png)")
        if path:
            exp = pg.exporters.ImageExporter(self.pplot.getPlotItem())
            exp.parameters()["width"] = 1600
            exp.export(path)
