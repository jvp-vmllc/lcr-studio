"""Meter controls: the connection bar (top bar), the settings strip (Measure tab) and the Meter menu."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QMenu, QPushButton, QToolButton, QWidget

from ..theme import repolish
from ..ut622e import BAUDS, DEMO_PORT, FREQUENCIES, LEVELS, find_ports
from ..widgets import Badge, Card, Segmented, ask, field_label


class ConnectionBar(QWidget):
    """Port, baud, Connect and the open/short correction badges."""

    connectRequested = Signal(str, int)
    disconnectRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.is_connected = False
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.port = QComboBox()
        self.port.setMinimumWidth(170)
        refresh = QPushButton("↻")
        refresh.setObjectName("Icon")
        refresh.setToolTip("Rescan serial ports")
        refresh.clicked.connect(self.refresh_ports)
        self.baud = QComboBox()
        for b in BAUDS:
            self.baud.addItem(str(b), b)
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setObjectName("Accent")
        self.connect_btn.clicked.connect(self._toggle_connect)
        self.open_badge = Badge("OPEN")
        self.short_badge = Badge("SHORT")
        self.open_badge.setToolTip("Open correction (done on the meter)")
        self.short_badge.setToolTip("Short correction (done on the meter)")
        for w in (self.port, refresh, self.baud, self.connect_btn):
            lay.addWidget(w)
        lay.addSpacing(8)
        lay.addWidget(field_label("Correction"))
        lay.addWidget(self.open_badge)
        lay.addWidget(self.short_badge)
        self.refresh_ports()
        self.set_connected(False)

    def _toggle_connect(self):
        if self.is_connected:
            self.disconnectRequested.emit()
        else:
            port = self.port.currentData()
            if port:
                self.connect_btn.setText("Connecting…")
                self.connect_btn.setEnabled(False)
                self.connectRequested.emit(port, self.baud.currentData())

    def refresh_ports(self, select: str | None = None):
        current = select or self.port.currentData()
        self.port.clear()
        for dev, desc, likely in find_ports():
            self.port.addItem(f"{dev}  ·  {'UT622E' if likely else desc}", dev)
        self.port.addItem("Demo (simulated)", DEMO_PORT)
        idx = self.port.findData(current) if current else 0
        self.port.setCurrentIndex(max(idx, 0))

    def select(self, port: str, baud: int):
        self.refresh_ports(select=port)
        i = self.baud.findData(baud)
        if i >= 0:
            self.baud.setCurrentIndex(i)

    def set_connected(self, on: bool, idn: str = ""):
        self.is_connected = on
        self.connect_btn.setEnabled(True)
        self.connect_btn.setText("Disconnect" if on else "Connect")
        self.connect_btn.setObjectName("" if on else "Accent")
        repolish(self.connect_btn)
        self.port.setEnabled(not on)
        self.baud.setEnabled(not on)
        if on:
            parts = idn.split(",")
            self.connect_btn.setToolTip(f"{parts[1] if len(parts) > 1 else idn}  ·  S/N {parts[2] if len(parts) > 2 else '?'}"
                                        f"  ·  FW {parts[3] if len(parts) > 3 else '?'}")
        else:
            self.connect_btn.setToolTip("")
            self.open_badge.set_kind("")
            self.short_badge.set_kind("")

    def apply_settings(self, st: dict):
        if "open_corr" in st:
            self.open_badge.set_kind("good" if st.get("open_corr") else "warn")
            self.short_badge.set_kind("good" if st.get("short_corr") else "warn")


class MeterSettings(QWidget):
    """Measurement, test signal, range and trigger cards side by side."""

    def __init__(self, worker, parent=None):
        super().__init__(parent)
        self.worker = worker
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        m = Card("Measurement")
        m.body.addWidget(field_label("Primary parameter"))
        self.primary = Segmented(["L", "C", "R", "Z", "DCR"])
        self.primary.changed.connect(lambda v: self._set(lambda mt: mt.set_primary(v)))
        m.body.addWidget(self.primary)
        m.body.addWidget(field_label("Secondary parameter"))
        self.secondary = Segmented([("D", "D"), ("Q", "Q"), ("X", "X"), ("DEG", "θ°"),
                                    ("RAD", "θ rad"), ("ESR", "ESR")], columns=6)
        self.secondary.changed.connect(lambda v: self._set(lambda mt: mt.set_secondary(v)))
        m.body.addWidget(self.secondary)
        m.body.addWidget(field_label("Circuit model"))
        self.equ = Segmented([("SER", "Series"), ("PAR", "Parallel")])
        self.equ.changed.connect(lambda v: self._set(lambda mt: mt.set_equivalent(v)))
        m.body.addWidget(self.equ)
        row.addWidget(m, 5)

        s = Card("Test signal")
        s.body.addWidget(field_label("Frequency"))
        self.freq = Segmented([(f, f.replace("Hz", "")) for f in FREQUENCIES])
        self.freq.changed.connect(lambda v: self._set(lambda mt: mt.set_frequency(v)))
        s.body.addWidget(self.freq)
        s.body.addWidget(field_label("Level"))
        self.level = Segmented(LEVELS)
        self.level.changed.connect(lambda v: self._set(lambda mt: mt.set_level(v)))
        s.body.addWidget(self.level)
        s.body.addWidget(field_label("Speed"))
        self.speed = Segmented([("SLOW", "Slow 2/s"), ("MED", "Med 5/s"), ("FAST", "Fast 20/s")])
        self.speed.changed.connect(lambda v: self._set(lambda mt: mt.set_speed(v)))
        s.body.addWidget(self.speed)
        row.addWidget(s, 5)

        r = Card("Range")
        self.range_auto = QPushButton("Auto range")
        self.range_auto.setCheckable(True)
        self.range_auto.clicked.connect(lambda on: self._set(lambda mt: mt.set_range_auto(on)))
        r.body.addWidget(self.range_auto)
        self.range = Segmented([(0, "100k"), (1, "10k"), (2, "1k"), (3, "100"), (4, "10")])
        self.range.setToolTip("Hold a fixed range (Ω)")
        self.range.changed.connect(lambda v: self._set(lambda mt: mt.set_range(v)))
        r.body.addWidget(self.range)
        r.body.addStretch(1)
        row.addWidget(r, 3)

        t = Card("Trigger")
        self.trigger = Segmented([("AUTO", "Continuous"), ("MAN", "Single")])
        self.trigger.changed.connect(lambda v: self._set(lambda mt: mt.set_trigger_auto(v == "AUTO")))
        t.body.addWidget(self.trigger)
        self.trig_btn = QPushButton("Measure once  (Space)")
        self.trig_btn.setObjectName("Accent")
        self.trig_btn.clicked.connect(self.worker.trigger)
        t.body.addWidget(self.trig_btn)
        self.sync = QCheckBox("Follow the meter's keys")
        self.sync.setChecked(True)
        self.sync.setToolTip("Pick up settings changed on the meter itself")
        self.sync.toggled.connect(lambda on: setattr(self.worker, "sync_front_panel", on))
        t.body.addWidget(self.sync)
        t.body.addStretch(1)
        row.addWidget(t, 3)

        self._cards = [m, s, r, t]
        self.set_connected(False)

    def _set(self, fn, refresh=True):
        self.worker.call(fn, tag="set", refresh=refresh)

    def set_connected(self, on: bool):
        for c in self._cards:
            c.setEnabled(on)

    def apply_settings(self, st: dict):
        self.primary.set_value(st.get("primary"))
        self.secondary.set_value(st.get("secondary"))
        self.equ.set_value(st.get("equivalent"))
        self.freq.set_value(st.get("frequency"))
        self.level.set_value(st.get("level"))
        self.speed.set_value(st.get("speed"))
        auto = bool(st.get("range_auto"))
        self.range_auto.setChecked(auto)
        self.range.set_value(None if auto else st.get("range"))
        self.trigger.set_value(st.get("trigger"))
        self.trig_btn.setEnabled(st.get("trigger") == "MAN")
        dcr = st.get("primary") == "DCR"
        for w in (self.secondary, self.equ, self.freq, self.level):
            w.setEnabled(not dcr)


class MeterMenu(QToolButton):
    """Top-bar menu: keypad lock toggle and settings reset."""

    def __init__(self, worker, parent=None):
        super().__init__(parent)
        self.worker = worker
        self.setText("Meter  ▾")
        self.setPopupMode(QToolButton.InstantPopup)
        self.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.setCursor(Qt.PointingHandCursor)
        menu = QMenu(self)
        self.lock_action = QAction("Lock meter keys", self)
        self.lock_action.setCheckable(True)
        self.lock_action.setToolTip("Holding the meter's power key 1 s also unlocks it")
        self.lock_action.toggled.connect(self._toggle_lock)
        menu.addAction(self.lock_action)
        menu.addSeparator()
        reset = QAction("Reset meter settings…", self)
        reset.triggered.connect(self._reset)
        menu.addAction(reset)
        self.setMenu(menu)
        self.set_connected(False)

    def _toggle_lock(self, on):
        self.lock_action.setText("Unlock meter keys" if on else "Lock meter keys")
        self.worker.call(lambda mt: mt.lock_keys() if on else mt.unlock_keys(), tag="set", refresh=False)

    def _reset(self):
        if ask(self, "Reset meter", "Reset the meter's measurement settings?\n"
                                    "Tolerance and recording modes on the meter will be turned off."):
            self.worker.call(lambda mt: mt.reset(), tag="set")

    def set_connected(self, on: bool):
        self.setEnabled(on)
        if not on:                                 # a fresh connection starts unlocked; send nothing
            self.lock_action.blockSignals(True)
            self.lock_action.setChecked(False)
            self.lock_action.blockSignals(False)
            self.lock_action.setText("Lock meter keys")
