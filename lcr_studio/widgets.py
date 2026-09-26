"""Reusable UI building blocks."""
from __future__ import annotations

import csv

import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from .engmath import fmt, parse_eng
from .theme import repolish, style_plot, theme


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


class EngEdit(QLineEdit):
    """Line edit that accepts engineering notation (4.7u, 10k, 2.2nF...)."""

    valueChanged = Signal(object)

    def __init__(self, placeholder: str = "", unit: str = "", parent=None):
        super().__init__(parent)
        self.unit = unit
        self.setPlaceholderText(placeholder)
        self.textChanged.connect(self._changed)

    def _changed(self):
        v = self.value()
        bad = bool(self.text().strip()) and v is None
        if bool(self.property("invalid")) != bad:
            self.setProperty("invalid", bad)
            repolish(self)
        self.valueChanged.emit(v)

    def value(self):
        try:
            return parse_eng(self.text()) if self.text().strip() else None
        except ValueError:
            return None

    def set_value(self, v, digits: int = 5):
        self.setText("" if v is None else fmt(v, self.unit, digits))


def make_plot(title_left: str = "", units_left: str = "", title_bottom: str = "", units_bottom: str = "",
              log_x: bool = False) -> pg.PlotWidget:
    w = pg.PlotWidget()
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
