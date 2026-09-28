"""Data logger: records readings with context, notes and export."""
from __future__ import annotations

import datetime as dt
import time

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QHBoxLayout, QHeaderView, QLineEdit,
                               QPushButton, QTableView, QVBoxLayout, QWidget)

from ..engmath import fmt
from ..theme import repolish
from ..ut622e import SECONDARY_LABEL, Reading
from ..widgets import Card, ask, export_table, field_label, muted
from .measure import fmt_secondary

HEADERS = ["#", "Time", "Elapsed (s)", "Primary", "Value", "Unit", "Secondary", "Value", "Unit",
           "Frequency", "Level", "Model", "Speed", "Compare", "Note"]


class LogModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self.rows = []      # (Reading, note, elapsed)

    def rowCount(self, parent=QModelIndex()):
        return len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return HEADERS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if role == Qt.TextAlignmentRole:
            return int(Qt.AlignRight | Qt.AlignVCenter) if index.column() in (0, 2, 4, 7) else None
        if role != Qt.DisplayRole:
            return None
        r, note, el = self.rows[index.row()]
        sv, su = fmt_secondary(r.stype, r.secondary)
        sv = f"{sv} {su}".strip()
        pv = "OL" if r.overload else fmt(r.primary, r.punit, 5)
        return [str(index.row() + 1), dt.datetime.fromtimestamp(r.t).strftime("%H:%M:%S.%f")[:-3],
                f"{el:.2f}", r.ptype, pv, r.punit, SECONDARY_LABEL.get(r.stype, "") if r.stype else "", sv,
                su or r.sunit, "DC" if r.ptype == "DCR" else r.frequency, r.level, r.equivalent, r.speed,
                r.compare or "", note][index.column()]

    def append(self, item):
        n = len(self.rows)
        self.beginInsertRows(QModelIndex(), n, n)
        self.rows.append(item)
        self.endInsertRows()

    def clear(self):
        self.beginResetModel()
        self.rows = []
        self.endResetModel()


class LogPanel(QWidget):
    recordingChanged = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.recording = False
        self._t0 = None
        self._last_rec = 0.0

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)
        ctl = Card("Recorder")
        row = QHBoxLayout()
        row.setSpacing(8)
        self.rec_btn = QPushButton("●  Start recording")
        self.rec_btn.setObjectName("Accent")
        self.rec_btn.clicked.connect(self.toggle)
        row.addWidget(self.rec_btn)
        row.addWidget(field_label("Mode"))
        self.mode = QComboBox()
        self.mode.addItem("Every reading", "all")
        self.mode.addItem("Interval", "interval")
        self.mode.addItem("Snapshots only", "manual")
        self.mode.currentIndexChanged.connect(lambda: self.interval.setEnabled(self.mode.currentData() == "interval"))
        row.addWidget(self.mode)
        self.interval = QDoubleSpinBox()
        self.interval.setRange(0.1, 86400)
        self.interval.setValue(1.0)
        self.interval.setSuffix(" s")
        self.interval.setEnabled(False)
        row.addWidget(self.interval)
        row.addWidget(field_label("Note"))
        self.note = QLineEdit()
        self.note.setPlaceholderText("Note for new rows")
        row.addWidget(self.note, 1)
        ctl.body.addLayout(row)
        row = QHBoxLayout()
        self.autoscroll = QCheckBox("Follow newest")
        self.autoscroll.setChecked(True)
        row.addWidget(self.autoscroll)
        self.count = muted("0 rows")
        row.addWidget(self.count, 1)
        exp = QPushButton("Export…")
        exp.clicked.connect(self._export)
        clr = QPushButton("Clear")
        clr.setObjectName("Danger")
        clr.clicked.connect(self._clear)
        row.addWidget(exp)
        row.addWidget(clr)
        ctl.body.addLayout(row)
        root.addWidget(ctl)

        tc = Card()
        self.model = LogModel()
        self.view = QTableView()
        self.view.setModel(self.model)
        self.view.setAlternatingRowColors(True)
        self.view.verticalHeader().hide()
        self.view.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.view.horizontalHeader().setStretchLastSection(True)
        self.view.verticalHeader().setDefaultSectionSize(24)
        self.view.setSelectionBehavior(QTableView.SelectRows)
        tc.body.addWidget(self.view)
        root.addWidget(tc, 1)

    def toggle(self):
        self.recording = not self.recording
        self.rec_btn.setText("■  Stop recording" if self.recording else "●  Start recording")
        self.rec_btn.setObjectName("Danger" if self.recording else "Accent")
        repolish(self.rec_btn)
        self._last_rec = 0.0
        self.recordingChanged.emit(self.recording)

    def on_reading(self, r: Reading):
        if not self.recording:
            return
        mode = self.mode.currentData()
        if mode == "manual":
            return
        if mode == "interval":
            now = time.monotonic()
            if now - self._last_rec < self.interval.value() - 0.02:
                return
            self._last_rec = now
        self.add(r)

    def add(self, r: Reading):
        if self._t0 is None:
            self._t0 = r.t
        self.model.append((r, self.note.text().strip(), r.t - self._t0))
        self.count.setText(f"{len(self.model.rows)} rows")
        if len(self.model.rows) == 1:
            self.view.resizeColumnsToContents()
        if self.autoscroll.isChecked():
            self.view.scrollToBottom()

    def _clear(self):
        if self.model.rows and not ask(self, "Clear log", f"Delete all {len(self.model.rows)} logged rows?"):
            return
        self.model.clear()
        self._t0 = None
        self.count.setText("0 rows")

    def _export(self):
        rows = []
        for i, (r, note, el) in enumerate(self.model.rows, 1):
            rows.append([i, dt.datetime.fromtimestamp(r.t).isoformat(timespec="milliseconds"), round(el, 3),
                         r.ptype, None if r.overload else r.primary, r.punit, r.stype, r.secondary, r.sunit,
                         r.freq_hz, r.level, r.equivalent, r.speed, r.compare, note])
        export_table(self, HEADERS, rows, f"lcr_log_{dt.datetime.now():%Y%m%d_%H%M%S}.xlsx")
