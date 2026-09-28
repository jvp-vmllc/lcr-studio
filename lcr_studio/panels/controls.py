"""Left sidebar: connection and every instrument setting."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QPushButton, QScrollArea, QVBoxLayout,
                               QWidget)

from ..ut622e import BAUDS, DEMO_PORT, FREQUENCIES, LEVELS, find_ports
from ..widgets import Badge, Card, Segmented, ask, field_label, muted


class ControlPanel(QScrollArea):
    connectRequested = Signal(str, int)
    disconnectRequested = Signal()

    def __init__(self, worker, parent=None):
        super().__init__(parent)
        self.worker = worker
        self.is_connected = False
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFixedWidth(318)
        inner = QWidget()
        self.setWidget(inner)
        col = QVBoxLayout(inner)
        col.setContentsMargins(12, 12, 6, 12)
        col.setSpacing(10)

        # --- connection
        c = Card("Connection")
        row = QHBoxLayout()
        self.port = QComboBox()
        self.port.setMinimumWidth(150)
        refresh = QPushButton("↻")
        refresh.setObjectName("Icon")
        refresh.setToolTip("Rescan serial ports")
        refresh.clicked.connect(self.refresh_ports)
        self.baud = QComboBox()
        for b in BAUDS:
            self.baud.addItem(str(b), b)
        row.addWidget(self.port, 1)
        row.addWidget(refresh)
        row.addWidget(self.baud)
        c.body.addLayout(row)
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setObjectName("Accent")
        self.connect_btn.clicked.connect(self._toggle_connect)
        c.body.addWidget(self.connect_btn)
        self.idn = muted("Not connected")
        c.body.addWidget(self.idn)
        corr = QHBoxLayout()
        corr.setSpacing(6)
        corr.addWidget(field_label("Correction"))
        self.open_badge = Badge("OPEN")
        self.short_badge = Badge("SHORT")
        self.open_badge.setToolTip("Open correction (done on the meter)")
        self.short_badge.setToolTip("Short correction (done on the meter)")
        corr.addWidget(self.open_badge)
        corr.addWidget(self.short_badge)
        corr.addStretch(1)
        c.body.addLayout(corr)
        col.addWidget(c)

        # --- measurement
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
        col.addWidget(m)

        # --- test signal
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
        col.addWidget(s)

        # --- range
        r = Card("Range")
        self.range_auto = QPushButton("Auto range")
        self.range_auto.setCheckable(True)
        self.range_auto.clicked.connect(lambda on: self._set(lambda mt: mt.set_range_auto(on)))
        r.body.addWidget(self.range_auto)
        self.range = Segmented([(0, "100k"), (1, "10k"), (2, "1k"), (3, "100"), (4, "10")])
        self.range.setToolTip("Hold a fixed range (Ω)")
        self.range.changed.connect(lambda v: self._set(lambda mt: mt.set_range(v)))
        r.body.addWidget(self.range)
        col.addWidget(r)

        # --- trigger
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
        col.addWidget(t)

        # --- front panel
        f = Card("Meter")
        row = QHBoxLayout()
        lock = QPushButton("Lock keys")
        lock.setToolTip("Lock the meter's keypad (hold its power key 1 s to unlock)")
        lock.clicked.connect(lambda: self._set(lambda mt: mt.lock_keys(), refresh=False))
        unlock = QPushButton("Unlock keys")
        unlock.setToolTip("Unlock the meter's keypad")
        unlock.clicked.connect(lambda: self._set(lambda mt: mt.unlock_keys(), refresh=False))
        row.addWidget(lock)
        row.addWidget(unlock)
        f.body.addLayout(row)
        reset = QPushButton("Reset meter settings")
        reset.setObjectName("Danger")
        reset.setToolTip("Reset the meter's measurement settings")
        reset.clicked.connect(self._reset)
        f.body.addWidget(reset)
        col.addWidget(f)
        col.addStretch(1)

        self._setting_widgets = [m, s, r, t, f]
        self.refresh_ports()
        self.set_connected(False)

    # ------------------------------------------------------------------
    def _set(self, fn, refresh=True):
        self.worker.call(fn, tag="set", refresh=refresh)

    def _reset(self):
        if ask(self, "Reset meter", "Reset the meter's measurement settings?\n"
                                    "Tolerance and recording modes on the meter will be turned off."):
            self._set(lambda mt: mt.reset())

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
            label = f"{dev}  ·  {'UT622E' if likely else desc}"
            self.port.addItem(label, dev)
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
        self.connect_btn.style().unpolish(self.connect_btn)
        self.connect_btn.style().polish(self.connect_btn)
        self.port.setEnabled(not on)
        self.baud.setEnabled(not on)
        for w in self._setting_widgets:
            w.setEnabled(on)
        if on:
            parts = idn.split(",")
            self.idn.setText(f"{parts[1] if len(parts) > 1 else idn}  ·  S/N {parts[2] if len(parts) > 2 else '?'}"
                             f"  ·  FW {parts[3] if len(parts) > 3 else '?'}")
        else:
            self.idn.setText("Not connected")
            self.open_badge.set_kind("")
            self.short_badge.set_kind("")

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
        if "open_corr" in st:
            self.open_badge.set_kind("good" if st.get("open_corr") else "warn")
            self.short_badge.set_kind("good" if st.get("short_corr") else "warn")
