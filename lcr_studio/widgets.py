"""Reusable UI building blocks."""
from __future__ import annotations

import csv
import math
import time

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QLocale, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QDoubleValidator, QPainter, QPen
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from .engmath import fmt
from .theme import repolish, style_plot, theme

pg.setConfigOptions(antialias=True)

REVEAL_S = 0.4      # seconds a newly measured chart point takes to grow in


class Card(QFrame):
    """Rounded surface with an optional small caps title."""

    def __init__(self, title: str = "", parent=None, spacing: int = 8):
        super().__init__(parent)
        self.setObjectName("Card")
        self.outer = QVBoxLayout(self)
        self.outer.setContentsMargins(14, 12, 14, 14)
        self.outer.setSpacing(spacing)
        self.header = QHBoxLayout()
        self.header.setSpacing(6)
        if title:
            self.title = QLabel(title.upper())
            self.title.setObjectName("CardTitle")
            self.header.addWidget(self.title)
            self.header.addStretch(1)
            self.outer.addLayout(self.header)
        self.body = self.outer


class Segmented(QWidget):
    """Row of mutually exclusive toggle buttons. Emits changed(value) on user clicks only."""

    changed = Signal(object)

    def __init__(self, options, parent=None, columns: int | None = None):
        super().__init__(parent)
        lay = QGridLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons = {}
        columns = columns or len(options)
        for i, opt in enumerate(options):
            value, label = opt if isinstance(opt, tuple) else (opt, str(opt))
            b = QPushButton(label)
            b.setObjectName("Seg")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            b.clicked.connect(lambda _=False, v=value: self.changed.emit(v))
            self.group.addButton(b)
            self.buttons[value] = b
            lay.addWidget(b, i // columns, i % columns)

    def set_value(self, value):
        b = self.buttons.get(value)
        if b is not None:
            b.setChecked(True)
        else:
            self.group.setExclusive(False)
            for btn in self.buttons.values():
                btn.setChecked(False)
            self.group.setExclusive(True)

    def value(self):
        for v, b in self.buttons.items():
            if b.isChecked():
                return v
        return None


class Badge(QLabel):
    def __init__(self, text: str = "", kind: str = "", parent=None):
        super().__init__(text, parent)
        self.setObjectName("Badge")
        self.set_kind(kind)

    def set_kind(self, kind: str):
        if self.property("kind") != kind:
            self.setProperty("kind", kind)
            repolish(self)


def field_label(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("FieldLabel")
    return lab


def muted(text: str = "") -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("Muted")
    lab.setWordWrap(True)
    return lab


PREFIX_SCALE = {"p": 1e-12, "n": 1e-9, "µ": 1e-6, "m": 1e-3, "": 1.0, "k": 1e3, "M": 1e6}


class UnitEdit(QWidget):
    """Number box plus a unit drop-down (620 | µH): nothing to type but the number, so no unit mistakes.

    value() is in base units (H, %, ...); None while empty or not positive. A required field is outlined
    in red until it holds a value.
    """

    valueChanged = Signal(object)

    def __init__(self, unit: str, prefixes=("n", "µ", "m", ""), default: str = "µ", required: bool = False,
                 parent=None):
        super().__init__(parent)
        self.unit, self.prefixes, self.required = unit, list(prefixes), required
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.edit = QLineEdit()
        validator = QDoubleValidator(0.0, 1e12, 6, self.edit)
        validator.setNotation(QDoubleValidator.StandardNotation)
        validator.setLocale(QLocale.c())
        self.edit.setValidator(validator)
        self.edit.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self.edit, 1)
        if len(self.prefixes) > 1:
            self.combo = QComboBox()
            for p in self.prefixes:
                self.combo.addItem(f"{p}{unit}", p)
            self.combo.setCurrentIndex(self.prefixes.index(default) if default in self.prefixes else 0)
            self.combo.currentIndexChanged.connect(lambda _i: self._changed())
            lay.addWidget(self.combo)
        else:
            self.combo = None
            lay.addWidget(field_label(f"{self.prefixes[0]}{unit}"))
        self.edit.textChanged.connect(lambda _t: self._changed())
        self._changed()

    def _scale(self) -> float:
        prefix = self.combo.currentData() if self.combo is not None else self.prefixes[0]
        return PREFIX_SCALE.get(prefix, 1.0)

    def value(self):
        try:
            n = float(self.edit.text().replace(",", "."))
        except ValueError:
            return None
        return n * self._scale() if n > 0 else None

    def set_value(self, v, *_):
        if v is None or v <= 0:
            self.edit.setText("")
            return
        if self.combo is not None:                  # largest prefix that keeps the number at 1 or more
            best = self.prefixes[0]
            for p in self.prefixes:
                if v / PREFIX_SCALE[p] >= 1:
                    best = p
            self.combo.blockSignals(True)
            self.combo.setCurrentIndex(self.prefixes.index(best))
            self.combo.blockSignals(False)
        self.edit.setText(f"{v / self._scale():.6g}")

    def setFocus(self):
        self.edit.setFocus()

    def _changed(self):
        v = self.value()
        bad = (bool(self.edit.text().strip()) and v is None) or (self.required and v is None)
        for target in (self, self.edit):
            if bool(target.property("invalid")) != bad:
                target.setProperty("invalid", bad)
                repolish(target)
        self.valueChanged.emit(v)


class Spinner(QWidget):
    """Rotating arc used as the busy indicator."""

    def __init__(self, size: int = 46, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.angle = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._step)
        self.timer.start(30)

    def _step(self):
        self.angle = (self.angle + 9) % 360
        self.update()

    def stop(self):
        self.timer.stop()
        self.update()

    def paintEvent(self, _ev):
        c = theme.c
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(4, 4, self.width() - 8, self.height() - 8)
        p.setPen(QPen(QColor(c["border"]), 4))
        p.drawEllipse(r)
        if self.timer.isActive():
            p.setPen(QPen(QColor(c["accent"]), 4, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(r, int(-self.angle * 16), 110 * 16)
        p.end()


class BusyDialog(QDialog):
    """Frameless pop-up shown while a long measurement runs: what is being measured, progress and Abort."""

    def __init__(self, kicker: str, title: str, on_abort, parent=None):
        super().__init__(parent, Qt.Dialog | Qt.FramelessWindowHint)
        self.setModal(True)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._on_abort = on_abort
        self._on_stop = None
        self._running = True
        frame = QFrame(self)
        frame.setObjectName("Card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)
        head = QHBoxLayout()
        head.setSpacing(16)
        self.spinner = Spinner()
        head.addWidget(self.spinner, 0, Qt.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        self.kicker = QLabel(kicker.upper())
        self.kicker.setObjectName("CardTitle")
        self.title = QLabel(title)
        self.title.setObjectName("AppTitle")
        self.detail = muted("Starting…")
        texts.addWidget(self.kicker)
        texts.addWidget(self.title)
        texts.addWidget(self.detail)
        head.addLayout(texts, 1)
        lay.addLayout(head)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        lay.addWidget(self.bar)
        row = QHBoxLayout()
        row.addStretch(1)
        self.abort_btn = QPushButton("Abort")
        self.abort_btn.clicked.connect(self._abort)
        row.addWidget(self.abort_btn)
        self.continue_btn = QPushButton("Continue")
        self.continue_btn.setObjectName("Accent")
        self.continue_btn.hide()
        row.addWidget(self.continue_btn)
        lay.addLayout(row)
        self.setMinimumWidth(440)

    def open_centered(self):
        self.adjustSize()
        top = self.parentWidget().window() if self.parentWidget() else None
        if top is not None:
            self.move(top.frameGeometry().center() - self.rect().center())
        self.show()

    def progress(self, i: int, n: int, detail: str):
        self.bar.setMaximum(max(n, 1))
        self.bar.setValue(i)
        self.detail.setText(detail)

    def finish(self, text: str, ok: bool = True, delay_ms: int = 900):
        """Show the outcome briefly, then close."""
        self._running = False
        self.spinner.stop()
        self.abort_btn.setEnabled(False)
        self.kicker.setText("DONE" if ok else "STOPPED")
        self.detail.setText(text)
        if ok:
            self.bar.setValue(self.bar.maximum())
        QTimer.singleShot(delay_ms, self.accept)

    def prompt(self, kicker: str, title: str, text: str, on_continue, on_stop):
        """Between steps: show what to wire up next and wait for Continue or Stop."""
        self._running = False
        self._on_stop = on_stop
        self.spinner.stop()
        self.kicker.setText(kicker.upper())
        self.title.setText(title)
        self.detail.setText(text)
        self.bar.hide()
        self.abort_btn.setText("Stop")
        self.abort_btn.setEnabled(True)
        self.continue_btn.clicked.connect(on_continue)
        self.continue_btn.show()
        self.continue_btn.setFocus()
        self.adjustSize()

    def _abort(self):
        self.abort_btn.setEnabled(False)
        if self._on_stop is not None:
            self._on_stop()
            return
        self.detail.setText("Stopping…")
        self._on_abort()

    def reject(self):                      # Esc acts as Abort / Stop instead of hiding the dialog
        if self._running or self.continue_btn.isVisible():
            self._abort()
        else:
            super().reject()


class SmoothRange:
    """Eases one axis of a plot toward a target range instead of snapping. Call tick() once per display frame."""

    K = 0.2          # fraction of the remaining distance covered per frame: settles in about 0.4 s at 30 fps

    def __init__(self, plot, axis: str = "y", padding: float = 0.08):
        self.plot, self.axis, self.padding = plot, axis, padding
        self.cur = None
        self.target = None

    def set_target(self, lo, hi):
        lo, hi = float(lo), float(hi)
        if hi <= lo:
            pad = abs(lo) * 1e-4 or 1e-12
            lo, hi = lo - pad, hi + pad
        self.target = (lo, hi)

    def clear(self):
        self.cur = self.target = None

    def tick(self) -> bool:
        """Apply one easing step; returns True while the range is still moving."""
        if self.target is None:
            return False
        lo, hi = self.target
        if self.cur is None or not all(math.isfinite(v) for v in self.cur):
            self.cur = (lo, hi)
        else:
            c0 = self.cur[0] + (lo - self.cur[0]) * self.K
            c1 = self.cur[1] + (hi - self.cur[1]) * self.K
            eps = (hi - lo) * 1e-3
            if abs(c0 - lo) < eps and abs(c1 - hi) < eps:
                c0, c1 = lo, hi
            self.cur = (c0, c1)
        if self.axis == "y":
            self.plot.setYRange(self.cur[0], self.cur[1], padding=self.padding)
        else:
            self.plot.setXRange(self.cur[0], self.cur[1], padding=self.padding)
        return self.cur != (lo, hi)


def range_with_limits(values, limits, near: float = 3.0):
    """Extent of the data, widened to include only those limits that lie within `near` data spans of it.

    Far-away limits stay off-screen so the curve keeps its shape; a limit comes into view as the data
    approaches it.
    """
    lo, hi = float(min(values)), float(max(values))
    span = (hi - lo) or abs(hi) * 0.02 or 1e-9
    for lim in limits:
        if lo - near * span <= lim <= hi + near * span:
            lo, hi = min(lo, lim), max(hi, lim)
    return lo, hi


def watch_manual_zoom(owner, *plots):
    """Clear owner._follow when the user zooms or pans one of the plots by hand."""
    for plot in plots:
        plot.getViewBox().sigRangeChangedManually.connect(lambda *_: setattr(owner, "_follow", False))


class EngAxis(pg.AxisItem):
    """Left axis whose log-mode ticks read in engineering units (12.0 µH, 1.00 mH) instead of 0.01 / 1."""

    def __init__(self, unit: str):
        super().__init__(orientation="left")
        self.unit = unit

    def logTickStrings(self, values, scale, spacing):
        return [fmt(10 ** v, self.unit, 3) for v in values]


class RollAxis(pg.AxisItem):
    """Bottom axis of a rolling strip chart: ticks sit at round numbers of seconds before `now` (0 = right edge)."""

    def __init__(self):
        super().__init__(orientation="bottom")
        self.now = 0.0

    def tickSpacing(self, minVal, maxVal, size):
        return [(sp, (self.now % sp) if sp else off) for sp, off in super().tickSpacing(minVal, maxVal, size)]

    def tickStrings(self, values, scale, spacing):
        return [f"{round((v - self.now) * scale, 6) + 0.0:g}" for v in values]


def make_plot(title_left: str = "", units_left: str = "", title_bottom: str = "", units_bottom: str = "",
              log_x: bool = False, bottom_axis: pg.AxisItem | None = None,
              left_axis: pg.AxisItem | None = None) -> pg.PlotWidget:
    axes = {k: v for k, v in (("bottom", bottom_axis), ("left", left_axis)) if v is not None}
    w = pg.PlotWidget(axisItems=axes or None)
    p = w.getPlotItem()
    p.setLabel("left", title_left, units=units_left)
    p.setLabel("bottom", title_bottom, units=units_bottom)
    p.setLogMode(x=log_x, y=False)
    for ax in ("left", "bottom"):
        p.getAxis(ax).enableAutoSIPrefix(True)
    p.getViewBox().setMouseMode(pg.ViewBox.RectMode)

    def restyle(c, w=w, p=p):
        w.setBackground(c["surface"])
        style_plot(p, c)
    restyle(theme.c)
    theme.changed.connect(restyle)
    w.setMinimumHeight(140)
    return w


def smooth_curve(x, y, log_x: bool = False, n: int = 160):
    """Dense monotone-cubic curve through (x, y): smooth between points, never overshooting them."""
    xa = np.asarray(x, dtype=float)
    ya = np.asarray(y, dtype=float)
    if len(xa) < 3:
        return xa, ya
    order = np.argsort(xa)
    xa, ya = xa[order], ya[order]
    xi = np.log10(xa) if log_x else xa
    h = np.diff(xi)
    if np.any(h <= 0) or not np.all(np.isfinite(ya)):
        return xa, ya
    d = np.diff(ya) / h
    m = np.empty_like(ya)
    m[0], m[-1] = d[0], d[-1]
    for i in range(1, len(xa) - 1):                 # Fritsch-Butland slopes keep the curve monotone
        if d[i - 1] * d[i] <= 0:
            m[i] = 0.0
        else:
            w1, w2 = 2 * h[i] + h[i - 1], h[i] + 2 * h[i - 1]
            m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i])
    xs = np.unique(np.concatenate([np.linspace(xi[0], xi[-1], n), xi]))   # knots included: markers sit on the line
    k = np.clip(np.searchsorted(xi, xs, side="right") - 1, 0, len(xi) - 2)
    t = (xs - xi[k]) / h[k]
    t2, t3 = t * t, t * t * t
    ys = ((2 * t3 - 3 * t2 + 1) * ya[k] + (t3 - 2 * t2 + t) * h[k] * m[k]
          + (-2 * t3 + 3 * t2) * ya[k + 1] + (t3 - t2) * h[k] * m[k + 1])
    return (10 ** xs if log_x else xs), ys


def reveal_fraction(t0: float):
    """Ease-out progress of a grow-in animation started at monotonic time t0: (0..1, finished)."""
    f = min(1.0, (time.monotonic() - t0) / REVEAL_S)
    return 1 - (1 - f) ** 3, f >= 1.0


def plot_series(plot, x, y, color, name=None, log_x: bool = False, style=Qt.SolidLine, width: int = 2,
                symbol_size: int = 7, reveal: float = 1.0):
    """Smooth line through the points plus a marker on every measured point. Returns the line item.

    reveal < 1 draws the newest point only part of the way from the previous one (grow-in animation).
    """
    x, y = list(x), list(y)
    tail = None
    if reveal < 1.0 and len(x) >= 2:
        if log_x:
            x[-1] = 10 ** (np.log10(x[-2]) + (np.log10(x[-1]) - np.log10(x[-2])) * reveal)
        else:
            x[-1] = x[-2] + (x[-1] - x[-2]) * reveal
        y[-1] = y[-2] + (y[-1] - y[-2]) * reveal
        tail = (x.pop(), y.pop())
    elif reveal < 1.0 and len(x) == 1:
        tail = (x.pop(), y.pop())                 # first point of a series just grows in place
    xs, ys = smooth_curve(x + ([tail[0]] if tail else []), y + ([tail[1]] if tail else []), log_x)
    line = plot.plot(xs, ys, pen=pg.mkPen(color, width=width, style=style), name=name)
    if x:
        plot.plot(x, y, pen=None, symbol="o", symbolSize=symbol_size, symbolBrush=color, symbolPen=None)
    if tail:
        plot.plot([tail[0]], [tail[1]], pen=None, symbol="o", symbolSize=max(1.0, symbol_size * reveal),
                  symbolBrush=color, symbolPen=None)
    return line


def ask(parent, title: str, text: str) -> bool:
    return QMessageBox.question(parent, title, text, QMessageBox.Yes | QMessageBox.No,
                                QMessageBox.No) == QMessageBox.Yes


def export_table(parent, headers, rows, default_name: str):
    """Ask for a path and save rows as CSV or Excel."""
    path, _ = QFileDialog.getSaveFileName(parent, "Export data", default_name,
                                          "Excel workbook (*.xlsx);;CSV (*.csv)")
    if not path:
        return None
    try:
        if path.lower().endswith(".xlsx"):
            from openpyxl import Workbook
            from openpyxl.styles import Font, PatternFill
            wb = Workbook()
            ws = wb.active
            ws.append(list(headers))
            for cell in ws[1]:
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="2563EB")
            for r in rows:
                ws.append(list(r))
            for i, h in enumerate(headers, 1):
                ws.column_dimensions[ws.cell(1, i).column_letter].width = max(11, len(str(h)) + 3)
            ws.freeze_panes = "A2"
            wb.save(path)
        else:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(headers)
                w.writerows(rows)
    except OSError as exc:
        QMessageBox.warning(parent, "Export failed", str(exc))
        return None
    return path
