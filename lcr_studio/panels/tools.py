"""Engineering calculators."""
from __future__ import annotations

import math

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)

from ..engmath import E_SERIES, fmt, nearest_standard, params_from_z, parse_eng, z_from_measurement
from ..ut622e import FREQ_HZ, Reading
from ..widgets import Card, EngEdit, Segmented, field_label, muted
from .measure import fmt_derived


class CalcCard(Card):
    """Card with labelled inputs on top and a grid of computed outputs below."""

    def __init__(self, title, note=""):
        super().__init__(title)
        if note:
            self.body.addWidget(muted(note))
        self.inputs = QGridLayout()
        self.inputs.setVerticalSpacing(6)
        self.body.addLayout(self.inputs)
        self.outputs = QGridLayout()
        self.outputs.setVerticalSpacing(4)
        self.outputs.setHorizontalSpacing(14)
        self.outputs.setColumnStretch(1, 1)
        self.outputs.setColumnStretch(3, 1)
        self.body.addLayout(self.outputs)
        self.body.addStretch(1)
        self.out = {}
        self._n_in = 0
        self._ready = False

    def add_input(self, label, widget, recalc):
        self.inputs.addWidget(field_label(label), self._n_in, 0)
        self.inputs.addWidget(widget, self._n_in, 1)
        self._n_in += 1
        sig = getattr(widget, "valueChanged", None) or getattr(widget, "changed", None) or \
            getattr(widget, "currentIndexChanged", None) or getattr(widget, "textChanged")
        sig.connect(lambda *_: self._ready and recalc())
        return widget

    def add_outputs(self, names, cols=2):
        for i, name in enumerate(names):
            r, c = divmod(i, cols)
            val = QLabel("—")
            val.setObjectName("DerivedValue")
            val.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.outputs.addWidget(field_label(name), r, c * 2)
            self.outputs.addWidget(val, r, c * 2 + 1)
            self.out[name] = val

    def set_out(self, **vals):
        for k, v in vals.items():
            self.out[k].setText(v)

    def blank(self):
        for v in self.out.values():
            v.setText("—")


def _v(edit):
    return edit.value()


class ToolsPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.last: Reading | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        inner = QWidget()
        scroll.setWidget(inner)
        grid = QGridLayout(inner)
        grid.setContentsMargins(6, 12, 12, 12)
        grid.setSpacing(10)
        grid.addWidget(self._analyzer(), 0, 0, 2, 1)
        grid.addWidget(self._resonance(), 0, 1)
        grid.addWidget(self._timeconst(), 1, 1)
        grid.addWidget(self._capacitor(), 2, 0)
        grid.addWidget(self._standard(), 2, 1)
        grid.addWidget(self._network(), 3, 0)
        grid.addWidget(self._dq(), 3, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(4, 1)
        for card in (self.an, self.rs, self.tc, self.cp, self.sv, self.nw, self.dq):
            card._ready = True

    # --------------------------------------------------------- analyzer --
    def _analyzer(self):
        c = CalcCard("Component analyzer",
                     "Convert between series and parallel models and derive every impedance parameter.")
        self.an_type = c.add_input("Component", Segmented([("C", "Capacitor"), ("L", "Inductor")]), self._calc_an)
        self.an_type.set_value("C")
        self.an_val = c.add_input("Value", EngEdit("e.g. 100n or 10u"), self._calc_an)
        self.an_loss = c.add_input("Loss given as", Segmented([("D", "D (tan δ)"), ("Q", "Q"), ("ESR", "ESR Ω")]),
                                   self._calc_an)
        self.an_loss.set_value("D")
        self.an_lossv = c.add_input("Loss value", EngEdit("e.g. 0.01"), self._calc_an)
        self.an_f = c.add_input("Frequency", EngEdit("Hz, e.g. 1k", "Hz"), self._calc_an)
        self.an_f.setText("1k")
        self.an_model = c.add_input("Value is", Segmented([("SER", "Series (Cs/Ls)"), ("PAR", "Parallel (Cp/Lp)")]),
                                    self._calc_an)
        self.an_model.set_value("SER")
        load = QPushButton("Load live reading")
        load.clicked.connect(self._load_live)
        c.inputs.addWidget(load, c._n_in, 1)
        c._n_in += 1
        self.an_msg = muted("")
        c.inputs.addWidget(self.an_msg, c._n_in, 0, 1, 2)
        c.add_outputs(["|Z|", "θ", "Rs", "Rp", "Xs", "Xp", "Cs", "Cp", "Ls", "Lp", "D", "Q"])
        self.an = c
        return c

    def _calc_an(self):
        kind, loss, model = self.an_type.value(), self.an_loss.value(), self.an_model.value()
        v, lv, f = _v(self.an_val), _v(self.an_lossv), _v(self.an_f)
        if None in (kind, loss, model, v, lv, f) or f <= 0 or v <= 0:
            self.an.blank()
            return
        z = z_from_measurement(kind, v, loss, lv, f, model)
        if z is None:
            self.an.blank()
            return
        p = params_from_z(z, f)
        self.an.set_out(**{k: fmt_derived(k, p[k]) for k in self.an.out})

    def _load_live(self):
        r = self.last
        if r is None or r.ptype not in ("C", "L") or r.stype not in ("D", "Q", "ESR"):
            self.an_msg.setText("Needs a live L or C reading with D, Q or ESR as secondary.")
            return
        self.an_msg.setText(f"Loaded {r.ptype} at {r.frequency}.")
        self.an_type.set_value(r.ptype)
        self.an_loss.set_value(r.stype)
        self.an_model.set_value(r.equivalent)
        self.an_val.setText(f"{r.primary:.6g}")
        self.an_lossv.setText(f"{r.secondary:.6g}")
        self.an_f.setText(f"{FREQ_HZ.get(r.frequency, 1000):g}")

    # -------------------------------------------------------- resonance --
    def _resonance(self):
        c = CalcCard("LC resonance & reactance")
        self.rs_l = c.add_input("L", EngEdit("e.g. 10u", "H"), self._calc_rs)
        self.rs_c = c.add_input("C", EngEdit("e.g. 100n", "F"), self._calc_rs)
        self.rs_f = c.add_input("f (for reactance)", EngEdit("e.g. 1k", "Hz"), self._calc_rs)
        c.add_outputs(["f₀", "ω₀", "Z₀ = √(L/C)", "Period", "X_L @ f", "X_C @ f"])
        self.rs = c
        return c

    def _calc_rs(self):
        L, C, f = _v(self.rs_l), _v(self.rs_c), _v(self.rs_f)
        self.rs.blank()
        if L and C and L > 0 and C > 0:
            f0 = 1 / (2 * math.pi * math.sqrt(L * C))
            self.rs.set_out(**{"f₀": fmt(f0, "Hz"), "ω₀": fmt(2 * math.pi * f0, "rad/s"),
                            "Z₀ = √(L/C)": fmt(math.sqrt(L / C), "Ω"), "Period": fmt(1 / f0, "s")})
        if f and f > 0:
            if L:
                self.rs.set_out(**{"X_L @ f": fmt(2 * math.pi * f * L, "Ω")})
            if C:
                self.rs.set_out(**{"X_C @ f": fmt(1 / (2 * math.pi * f * C), "Ω")})

    # ---------------------------------------------------- time constant --
    def _timeconst(self):
        c = CalcCard("RC / RL time constant & cutoff")
        self.tc_r = c.add_input("R", EngEdit("e.g. 10k", "Ω"), self._calc_tc)
        self.tc_c = c.add_input("C", EngEdit("e.g. 100n", "F"), self._calc_tc)
        self.tc_l = c.add_input("L", EngEdit("e.g. 1m", "H"), self._calc_tc)
        c.add_outputs(["τ (RC)", "f_c (RC)", "5τ (RC)", "τ (L/R)", "f_c (RL)", "5τ (RL)"])
        self.tc = c
        return c

    def _calc_tc(self):
        R, C, L = _v(self.tc_r), _v(self.tc_c), _v(self.tc_l)
        self.tc.blank()
        if R and C and R > 0 and C > 0:
            t = R * C
            self.tc.set_out(**{"τ (RC)": fmt(t, "s"), "f_c (RC)": fmt(1 / (2 * math.pi * t), "Hz"),
                            "5τ (RC)": fmt(5 * t, "s")})
        if R and L and R > 0 and L > 0:
            t = L / R
            self.tc.set_out(**{"τ (L/R)": fmt(t, "s"), "f_c (RL)": fmt(1 / (2 * math.pi * t), "Hz"),
                            "5τ (RL)": fmt(5 * t, "s")})

    # -------------------------------------------------------- capacitor --
    def _capacitor(self):
        c = CalcCard("Capacitor energy & ripple heating")
        self.cp_c = c.add_input("C", EngEdit("e.g. 470u", "F"), self._calc_cp)
        self.cp_v = c.add_input("Voltage", EngEdit("e.g. 25", "V"), self._calc_cp)
        self.cp_esr = c.add_input("ESR", EngEdit("e.g. 50m", "Ω"), self._calc_cp)
        self.cp_i = c.add_input("Ripple current (rms)", EngEdit("e.g. 1.2", "A"), self._calc_cp)
        c.add_outputs(["Energy", "Charge", "ESR loss", "ESR ripple V"])
        self.cp = c
        return c

    def _calc_cp(self):
        C, V, esr, I = _v(self.cp_c), _v(self.cp_v), _v(self.cp_esr), _v(self.cp_i)
        self.cp.blank()
        if C and V is not None:
            self.cp.set_out(Energy=fmt(0.5 * C * V * V, "J"), Charge=fmt(C * V, "C"))
        if esr is not None and I is not None:
            self.cp.set_out(**{"ESR loss": fmt(I * I * esr, "W"), "ESR ripple V": fmt(I * esr, "V")})

    # --------------------------------------------------------- standard --
    def _standard(self):
        c = CalcCard("Standard values & tolerance")
        self.sv_val = c.add_input("Measured", EngEdit("e.g. 4.62k"), self._calc_sv)
        self.sv_nom = c.add_input("Nominal (optional)", EngEdit("e.g. 4.7k"), self._calc_sv)
        use = QPushButton("Use live reading")
        use.clicked.connect(lambda: self.last and self.sv_val.setText(f"{self.last.primary:.6g}"))
        c.inputs.addWidget(use, c._n_in, 1)
        c._n_in += 1
        c.add_outputs(["Δ nominal", "Nearest E6", "Nearest E12", "Nearest E24", "Nearest E48", "Nearest E96"], cols=1)
        self.sv = c
        return c

    def _calc_sv(self):
        v, nom = _v(self.sv_val), _v(self.sv_nom)
        self.sv.blank()
        if v and v > 0:
            for s in E_SERIES:
                n = nearest_standard(v, s)
                self.sv.set_out(**{f"Nearest {s}": f"{fmt(n, '', 3)}   ({(v / n - 1) * 100:+.2f} %)"})
        if v and nom:
            self.sv.set_out(**{"Δ nominal": f"{(v / nom - 1) * 100:+.3f} %   ({fmt(v - nom, '')})"})

    # ---------------------------------------------------------- network --
    def _network(self):
        c = CalcCard("Series / parallel combination", "Comma-separated values, e.g. 10k, 4.7k, 2.2k")
        self.nw_kind = c.add_input("Type", Segmented([("R", "R / L"), ("C", "C")]), self._calc_nw)
        self.nw_kind.set_value("R")
        self.nw_vals = c.add_input("Values", QLineEdit(), self._calc_nw)
        c.add_outputs(["Series", "Parallel"], cols=1)
        self.nw = c
        return c

    def _calc_nw(self):
        self.nw.blank()
        try:
            vals = [parse_eng(x) for x in self.nw_vals.text().split(",") if x.strip()]
        except ValueError:
            return
        if not vals or any(v <= 0 for v in vals):
            return
        add = sum(vals)
        inv = 1 / sum(1 / v for v in vals)
        if self.nw_kind.value() == "C":
            add, inv = inv, add
        self.nw.set_out(Series=fmt(add, ""), Parallel=fmt(inv, ""))

    # --------------------------------------------------------------- DQ --
    def _dq(self):
        c = CalcCard("Loss converter", "D = tan δ = 1/Q.  Enter any one.")
        self.dq_kind = QComboBox()
        for k in ["D (tan δ)", "Q", "δ loss angle (°)", "θ phase angle (°)", "Power factor"]:
            self.dq_kind.addItem(k)
        c.add_input("Given", self.dq_kind, self._calc_dq)
        self.dq_val = c.add_input("Value", EngEdit("e.g. 0.02"), self._calc_dq)
        c.add_outputs(["D", "Q", "δ", "|θ|", "PF (cos θ)"])
        self.dq = c
        return c

    def _calc_dq(self):
        v = _v(self.dq_val)
        self.dq.blank()
        if v is None or v < 0:
            return
        k = self.dq_kind.currentIndex()
        try:
            if k == 0:
                d = v
            elif k == 1:
                d = 1 / v
            elif k == 2:
                d = math.tan(math.radians(v))
            elif k == 3:
                d = 1 / math.tan(math.radians(v))
            else:
                d = v / math.sqrt(1 - v * v)
        except (ZeroDivisionError, ValueError):
            return
        delta = math.degrees(math.atan(d))
        self.dq.set_out(D=f"{d:.6g}", Q=f"{1 / d:.6g}" if d else "∞", **{"δ": f"{delta:.4f} °",
                     "|θ|": f"{90 - delta:.4f} °", "PF (cos θ)": f"{math.sin(math.radians(delta)):.6g}"})

    def on_reading(self, r: Reading):
        self.last = r
