"""Raw SCPI terminal with traffic monitor."""
from __future__ import annotations

import datetime as dt

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLineEdit, QListWidget, QPlainTextEdit, QPushButton,
                               QVBoxLayout, QWidget)

from ..theme import pick_font, theme
from ..widgets import Card, muted

QUICK = [
    ("*IDN?", "Identify"), ("FETC?", "Read measurement"), ("*TRG", "Trigger + read"), ("*OPC?", "Operation complete"),
    ("FUNC:IMPA?", "Primary parameter"), ("FUNC:IMPB?", "Secondary parameter"), ("FUNC:EQU?", "Equivalent circuit"),
    ("FUNC:RANG?", "Range number"), ("FUNC:RANG:AUTO?", "Auto range?"), ("FREQ?", "Frequency"),
    ("VOLT?", "Test level"), ("APER?", "Speed"), ("TRIG:SOUR?", "Trigger source"), ("FETC:AUTO?", "Auto-return?"),
    ("COMP?", "Tolerance mode"), ("COMP:NOM?", "Nominal"), ("COMP:TOL?", "Tolerance"), ("COMP:ALAR?", "Alarm"),
    ("COMP:ALAR:SOUN?", "Alarm sound"), ("COMP:ALAR:LED?", "Alarm LED"), ("COMP:COUN?", "Counter"),
    ("CORR:OPEN?", "Open correction (undocumented)"), ("CORR:SHOR?", "Short correction (undocumented)"),
    ("*LLO", "Lock keypad"), ("*GTL", "Unlock keypad"),
]


class ConsolePanel(QWidget):
    def __init__(self, worker, parent=None):
        super().__init__(parent)
        self.worker = worker
        self.history = []
        self.hpos = 0
        root = QHBoxLayout(self)
        root.setContentsMargins(6, 12, 12, 12)
        root.setSpacing(10)

        term = Card("SCPI terminal")
        self.show_poll = QCheckBox("Show polling traffic")
        term.header.addWidget(self.show_poll)
        clear = QPushButton("Clear")
        term.header.addWidget(clear)
        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        self.out.setMaximumBlockCount(5000)
        f = QFont(pick_font(["Cascadia Mono", "Consolas", "JetBrains Mono", "DejaVu Sans Mono", "Monospace"]))
        f.setStyleHint(QFont.Monospace)
        f.setPointSize(10)
        self.out.setFont(f)
        clear.clicked.connect(self.out.clear)
        term.body.addWidget(self.out, 1)
        row = QHBoxLayout()
        self.cmd = QLineEdit()
        self.cmd.setFont(f)
        self.cmd.setPlaceholderText("Type a command, e.g. FREQ 10kHz or FREQ?  —  ↑/↓ for history")
        self.cmd.returnPressed.connect(self.send)
        self.cmd.installEventFilter(self)
        send = QPushButton("Send")
        send.setObjectName("Accent")
        send.clicked.connect(self.send)
        row.addWidget(self.cmd, 1)
        row.addWidget(send)
        term.body.addLayout(row)
        root.addWidget(term, 1)

        qc = Card("Quick commands")
        qc.setFixedWidth(300)
        self.quick = QListWidget()
        for cmd, desc in QUICK:
            self.quick.addItem(f"{cmd}    — {desc}")
        self.quick.itemDoubleClicked.connect(lambda it: (self.cmd.setText(it.text().split()[0]), self.send()))
        self.quick.itemClicked.connect(lambda it: self.cmd.setText(it.text().split()[0]))
        qc.body.addWidget(muted("Click to insert, double-click to send. Full reference: PROTOCOL.md"))
        qc.body.addWidget(self.quick, 1)
        root.addWidget(qc)

    def eventFilter(self, obj, ev):
        if obj is self.cmd and ev.type() == ev.Type.KeyPress and self.history:
            if ev.key() == Qt.Key_Up:
                self.hpos = max(0, self.hpos - 1)
                self.cmd.setText(self.history[self.hpos])
                return True
            if ev.key() == Qt.Key_Down:
                self.hpos = min(len(self.history), self.hpos + 1)
                self.cmd.setText(self.history[self.hpos] if self.hpos < len(self.history) else "")
                return True
        return super().eventFilter(obj, ev)

    def send(self):
        cmd = self.cmd.text().strip()
        if not cmd:
            return
        if not self.history or self.history[-1] != cmd:
            self.history.append(cmd)
        self.hpos = len(self.history)
        self.cmd.clear()
        self.worker.call(lambda m: m.raw(cmd), tag="console", refresh="full")

    def _append(self, html):
        self.out.appendHtml(html)
        self.out.moveCursor(QTextCursor.End)

    def on_traffic(self, direction, text, quiet):
        if quiet and not self.show_poll.isChecked():
            return
        c = theme.c
        color = c["accent"] if direction == ">" else c["good"]
        t = dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        esc = text.replace("&", "&amp;").replace("<", "&lt;")
        self._append(f'<span style="color:{c["muted"]}">{t}</span> '
                     f'<span style="color:{color}">{"→" if direction == ">" else "←"}</span> {esc}')

    def on_error(self, tag, msg):
        if tag == "console":
            self._append(f'<span style="color:{theme.c["bad"]}">✗ {msg}</span>')
