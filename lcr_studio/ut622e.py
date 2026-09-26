"""Driver for the UNI-T UT622E handheld LCR meter.

Protocol (UT622 Series User Manual pp. 31-39, verified on firmware Ver1.3.2142):
SCPI over a CH340 USB-serial link, 9600/19200/38400 8N1. Commands are ASCII
terminated by '\\n'; queries answer one line terminated by '\\r\\n'; set
commands answer nothing, so writes are followed by *OPC? to stay in lock-step.
See PROTOCOL.md for the full command table.
"""
from __future__ import annotations

import math
import os
import random
import time
from dataclasses import dataclass, field

import serial
import serial.tools.list_ports

FREQUENCIES = ["100Hz", "120Hz", "1kHz", "10kHz", "100kHz"]
FREQ_HZ = {"100Hz": 100.0, "120Hz": 120.0, "1kHz": 1e3, "10kHz": 1e4, "100kHz": 1e5}
LEVELS = ["0.1V", "0.3V", "1.0V"]
SPEEDS = ["SLOW", "MED", "FAST"]
PRIMARIES = ["L", "C", "R", "Z", "DCR"]
SECONDARIES = ["D", "Q", "X", "DEG", "RAD", "ESR"]
RANGES = ["100 kΩ", "10 kΩ", "1 kΩ", "100 Ω", "10 Ω"]
ALARMS = ["OFF", "PASS", "FAIL"]
SOUNDS = ["SHORT", "LONG", "DUAL"]
BAUDS = [9600, 19200, 38400]

PRIMARY_UNIT = {"L": "H", "C": "F", "R": "Ω", "Z": "Ω", "DCR": "Ω"}
SECONDARY_UNIT = {"D": "", "Q": "", "X": "Ω", "DEG": "°", "RAD": "rad", "ESR": "Ω"}
SECONDARY_LABEL = {"D": "D", "Q": "Q", "X": "X", "DEG": "θ", "RAD": "θ", "ESR": "ESR"}
# Upper display limits from the manual's display-range table; beyond = overload.
PRIMARY_LIMIT = {"L": 9999.9, "C": 99.999e-3, "R": 99.999e6, "Z": 99.999e6, "DCR": 999.99e3}

DEMO_PORT = "DEMO"


class MeterError(Exception):
    pass


@dataclass
class Reading:
    primary: float
    secondary: float | None
    compare: str | None          # "PASS", "FAIL" or None (not compared)
    ptype: str
    stype: str | None
    frequency: str | None
    level: str | None
    equivalent: str | None
    speed: str | None
    t: float = field(default_factory=time.time)

    @property
    def overload(self) -> bool:
        limit = PRIMARY_LIMIT.get(self.ptype)
        return limit is not None and abs(self.primary) > limit * 1.0001

    @property
    def freq_hz(self) -> float | None:
        return FREQ_HZ.get(self.frequency) if self.ptype != "DCR" else None

    @property
    def punit(self) -> str:
        return PRIMARY_UNIT.get(self.ptype, "")

    @property
    def sunit(self) -> str:
        return SECONDARY_UNIT.get(self.stype, "")


def find_ports():
    """Return [(device, description, is_likely_meter)], likely meters first."""
    out = []
    for p in serial.tools.list_ports.comports():
        likely = (p.vid == 0x1A86 and p.pid == 0x7523) or "UT622" in (p.description or "")
        out.append((p.device, p.description or p.device, likely))
    out.sort(key=lambda x: (not x[2], x[0]))
    return out


# ---------------------------------------------------------------- parsing --

def _flag(v: str) -> bool:
    return v.strip().upper() in ("1", "ON", "AUTO")


def _norm_choice(v: str, choices) -> str:
    for c in choices:
        if c.upper() == v.strip().upper():
            return c
    return v.strip()


def _norm_level(v: str) -> str:
    try:
        return f"{float(v.strip().upper().rstrip('V')):.1f}V"
    except ValueError:
        return v.strip()


def _norm_alarm(v: str) -> str:
    v = v.strip().upper()
    return {"0": "OFF", "1": "PASS", "2": "FAIL"}.get(v, v)


def _norm_sound(v: str) -> str:
    v = v.strip().upper()
    return {"0": "SHORT", "1": "LONG", "2": "DUAL"}.get(v, v)


def parse_fetch(text: str):
    parts = [p.strip() for p in text.split(",")]
    if len(parts) < 2:
        raise MeterError(f"Unexpected reading {text!r}")
    try:
        primary = float(parts[0])
        secondary = float(parts[1])
    except ValueError as exc:
        raise MeterError(f"Unexpected reading {text!r}") from exc
    cmp = {"0": "FAIL", "1": "PASS"}.get(parts[2].upper()) if len(parts) > 2 else None
    return primary, secondary, cmp


CORE_QUERIES = {
    "primary": ("FUNC:IMPA?", lambda v: v.strip().upper()),
    "secondary": ("FUNC:IMPB?", lambda v: None if v.strip().upper() == "NULL" else v.strip().upper()),
    "equivalent": ("FUNC:EQU?", lambda v: v.strip().upper()[:3]),
    "range_auto": ("FUNC:RANG:AUTO?", _flag),
    "range": ("FUNC:RANG?", lambda v: int(v.strip().upper().lstrip("R"))),
    "frequency": ("FREQ?", lambda v: _norm_choice(v, FREQUENCIES)),
    "level": ("VOLT?", _norm_level),
    "speed": ("APER?", lambda v: v.strip().upper()),
    "trigger": ("TRIG:SOUR?", lambda v: "AUTO" if v.strip().upper().startswith(("AUTO", "INT")) else "MAN"),
}
EXTRA_QUERIES = {
    "open_corr": ("CORR:OPEN?", _flag),
    "short_corr": ("CORR:SHOR?", _flag),
    "comp": ("COMP?", _flag),
    "comp_nominal": ("COMP:NOM?", float),
    "comp_tol": ("COMP:TOL?", lambda v: float(v.strip().rstrip("%"))),
    "alarm": ("COMP:ALAR?", _norm_alarm),
    "sound": ("COMP:ALAR:SOUN?", _norm_sound),
    "led": ("COMP:ALAR:LED?", _flag),
    "counter": ("COMP:COUN?", _flag),
}


# ----------------------------------------------------------------- driver --

class UT622E:
    """Blocking driver; not thread-safe (the GUI owns it from one worker thread)."""

    def __init__(self, port: str, baud: int = 9600, timeout: float = 2.0, log=None):
        self.port = port
        self.log = log  # callable(direction, text, quiet) or None
        extra = {"exclusive": True} if os.name == "posix" else {}
        self.ser = serial.Serial(port, baud, timeout=timeout, write_timeout=2.0, **extra)
        time.sleep(0.05)
        self.ser.reset_input_buffer()

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass

    # --- transport -------------------------------------------------------
    def _send(self, cmd: str, quiet: bool):
        if self.log:
            self.log(">", cmd, quiet)
        self.ser.write((cmd + "\n").encode("ascii"))

    def query(self, cmd: str, quiet: bool = False) -> str:
        self.ser.reset_input_buffer()
        self._send(cmd, quiet)
        line = self.ser.readline()
        if not line.endswith(b"\n"):
            raise MeterError(f"No response to {cmd}")
        text = line.decode("ascii", "replace").strip()
        if self.log:
            self.log("<", text, quiet)
        return text

    def write(self, cmd: str):
        self._send(cmd, False)
        self.query("*OPC?", quiet=True)

    def raw(self, cmd: str) -> str:
        """Send an arbitrary command; returns the reply or '(ok)' for set commands."""
        cmd = cmd.strip()
        if "?" in cmd or cmd.upper().startswith("*TRG"):
            return self.query(cmd)
        self.write(cmd)
        return "(ok)"

    # --- identity & readings ---------------------------------------------
    def idn(self) -> str:
        return self.query("*IDN?")

    def fetch(self):
        return parse_fetch(self.query("FETC?", quiet=True))

    def trigger_fetch(self):
        return parse_fetch(self.query("*TRG"))

    # --- settings --------------------------------------------------------
    def read_settings(self, full: bool = True) -> dict:
        out = {}
        queries = dict(CORE_QUERIES, **(EXTRA_QUERIES if full else {}))
        for key, (cmd, parse) in queries.items():
            try:
                out[key] = parse(self.query(cmd, quiet=True))
            except (ValueError, MeterError):
                out[key] = None
        return out

    def set_primary(self, p): self.write(f"FUNC:IMPA {p}")
    def set_secondary(self, s): self.write(f"FUNC:IMPB {s}")
    def set_equivalent(self, e): self.write(f"FUNC:EQU {e}")
    def set_range_auto(self, on=True): self.write(f"FUNC:RANG:AUTO {'ON' if on else 'OFF'}")
    def set_range(self, n: int): self.write(f"FUNC:RANG {int(n)}")
    def set_frequency(self, f): self.write(f"FREQ {f}")
    def set_level(self, v): self.write(f"VOLT {v}")
    def set_speed(self, s): self.write(f"APER {s}")
    def set_trigger_auto(self, on=True): self.write(f"TRIG:SOUR {'AUTO' if on else 'MAN'}")
    def set_fetch_auto(self, on=False): self.write(f"FETC:AUTO {'ON' if on else 'OFF'}")

    def set_comp(self, on): self.write(f"COMP {'ON' if on else 'OFF'}")
    def set_comp_nominal(self, v: float): self.write(f"COMP:NOM {v:.6e}")
    def set_comp_tol(self, pct: int): self.write(f"COMP:TOL {int(pct)}")
    def set_alarm(self, a): self.write(f"COMP:ALAR {a}")
    def set_sound(self, s): self.write(f"COMP:ALAR:SOUN {s}")
    def set_led(self, on): self.write(f"COMP:ALAR:LED {'ON' if on else 'OFF'}")
    def set_counter(self, on): self.write(f"COMP:COUN {'ON' if on else 'OFF'}")

    def lock_keys(self): self.write("*LLO")
    def unlock_keys(self): self.write("*GTL")
    def reset(self): self.write("*RST")


# -------------------------------------------------------------- simulator --

class SimulatedUT622E(UT622E):
    """Software stand-in for the meter: a 4.7 µF capacitor with ESR, ESL and leakage."""

    C, ESR, ESL, RLEAK = 4.7e-6, 0.08, 12e-9, 50e6

    def __init__(self, port=DEMO_PORT, baud=9600, timeout=2.0, log=None):
        self.port = port
        self.log = log
        self.s = {
            "primary": "C", "secondary": "D", "equivalent": "SER", "range_auto": True,
            "range": 2, "frequency": "1kHz", "level": "0.3V", "speed": "MED", "trigger": "AUTO",
            "open_corr": True, "short_corr": True, "comp": False, "comp_nominal": 0.0,
            "comp_tol": 5.0, "alarm": "OFF", "sound": "SHORT", "led": False, "counter": False,
        }
        self._last = time.monotonic()
        self.parts_mode = False        # DEMO:PARTS ON -> random parts are inserted and removed
        self._part_c = self.C
        self._part_since = time.monotonic()

    def close(self):
        pass

    def query(self, cmd, quiet=False):
        if self.log:
            self.log(">", cmd, quiet)
        reply = self._answer(cmd.strip())
        if self.log and reply is not None:
            self.log("<", reply, quiet)
        if reply is None:
            raise MeterError(f"No response to {cmd}")
        return reply

    def write(self, cmd):
        if self.log:
            self.log(">", cmd, False)
        self._apply(cmd.strip())

    def _answer(self, cmd):
        u = cmd.upper()
        s = self.s
        table = {
            "*IDN?": "UNI_T,UT622E,DEMO000000,Simulator", "*OPC?": "1",
            "FUNC:IMPA?": s["primary"], "FUNC:IMPB?": "NULL" if s["primary"] == "DCR" else s["secondary"].title() if s["secondary"] in ("DEG", "RAD") else s["secondary"],
            "FUNC:EQU?": s["equivalent"], "FUNC:RANG:AUTO?": "AUTO" if s["range_auto"] else "HOLD",
            "FUNC:RANG?": f"R{s['range']}", "FREQ?": s["frequency"], "VOLT?": s["level"],
            "APER?": s["speed"], "TRIG:SOUR?": s["trigger"], "CORR:OPEN?": "1", "CORR:SHOR?": "1",
            "COMP?": "1" if s["comp"] else "0", "COMP:NOM?": f"{s['comp_nominal']:.6e}",
            "COMP:TOL?": f"{s['comp_tol']:.1f}%", "COMP:ALAR?": s["alarm"], "COMP:ALAR:SOUN?": s["sound"],
            "COMP:ALAR:LED?": "1" if s["led"] else "0", "COMP:COUN?": "1" if s["counter"] else "0",
            "FETC:AUTO?": "0", "SER?": "DEMO000000",
        }
        if u in ("FETC?", "*TRG"):
            return self._measure(wait=(u == "FETC?"))
        return table.get(u)

    def _apply(self, cmd):
        head, _, arg = cmd.partition(" ")
        head, arg = head.upper(), arg.strip().upper()
        s = self.s
        on = arg in ("ON", "1")
        if head == "FUNC:IMPA": s["primary"] = arg
        elif head == "FUNC:IMPB": s["secondary"] = arg
        elif head == "FUNC:EQU": s["equivalent"] = arg[:3]
        elif head == "FUNC:RANG:AUTO": s["range_auto"] = on
        elif head == "FUNC:RANG": s["range"], s["range_auto"] = int(arg), False
        elif head == "FREQ": s["frequency"] = _norm_choice(arg, FREQUENCIES)
        elif head == "VOLT": s["level"] = _norm_level(arg)
        elif head == "APER": s["speed"] = {"SHORT": "FAST", "LONG": "SLOW", "MEDIUM": "MED"}.get(arg, arg)
        elif head == "TRIG:SOUR": s["trigger"] = "AUTO" if arg in ("AUTO", "INT", "INTERNAL") else "MAN"
        elif head == "COMP": s["comp"] = on
        elif head == "COMP:NOM": s["comp_nominal"] = float(arg)
        elif head == "COMP:TOL": s["comp_tol"] = float(arg)
        elif head == "COMP:ALAR": s["alarm"] = _norm_alarm(arg)
        elif head == "COMP:ALAR:SOUN": s["sound"] = _norm_sound(arg)
        elif head == "COMP:ALAR:LED": s["led"] = on
        elif head == "COMP:COUN": s["counter"] = on
        elif head == "DEMO:PARTS": self.parts_mode = on

    def set_fixture(self, fixture: str | None):
        """Simulator only: which flyback connection the operator has made (None = demo capacitor)."""
        self.fixture = fixture

    # Demo flyback transformer: 620 µH gapped primary, 9.2 µH leakage, secondary + aux + two spares.
    FB_LP, FB_LLK, FB_RDC = 620e-6, 9.2e-6, 0.42
    FB_TURNS = [6.2, 4.1, 8.0, 3.0]
    FB_SEC_RDC = [0.011, 0.36, 0.02, 0.5]

    def _dut(self, w, f, volts):
        """Impedance and DC resistance of whatever is on the test leads."""
        level = 1 + 0.024 * (volts - 0.1)                 # permeability rises slightly with drive level
        skin = 1 + 0.35 * math.sqrt(f / 1e5)              # AC winding resistance
        fx = getattr(self, "fixture", None)
        if fx == "PRI_OPEN":
            lm = self.FB_LP * level
            zm = 1 / (1 / (1j * w * lm) + 1 / 85e3 + 1j * w * 42e-12)   # core loss + winding capacitance
            return self.FB_RDC * skin + zm, self.FB_RDC
        if fx == "PRI_SHORT":
            llk = self.FB_LLK * (1 + 0.004 * (volts - 0.1))
            r = (self.FB_RDC + self.FB_SEC_RDC[0] * self.FB_TURNS[0] ** 2 * 0.5) * (1 + 0.9 * math.sqrt(f / 1e5))
            return complex(r, w * llk), self.FB_RDC
        if fx and fx.startswith("SEC"):
            i = int(fx[3:]) % len(self.FB_TURNS)
            n2 = self.FB_TURNS[i] ** 2
            ls = self.FB_LP * level / n2
            zm = 1 / (1 / (1j * w * ls) + n2 / 85e3)
            return self.FB_SEC_RDC[i] * skin + zm, self.FB_SEC_RDC[i]
        c = self.C
        if self.parts_mode:                      # 0.6 s empty fixture, then a part for 2.4 s
            age = time.monotonic() - self._part_since
            if age > 3.0:
                self._part_since = time.monotonic()
                self._part_c = self.C * (1 + random.gauss(0, 0.012))
                age = 0.0
            c = 2e-13 if age < 0.6 else self._part_c
        zc = 1 / (1j * w * c)
        return self.ESR + 1j * w * self.ESL + (zc * self.RLEAK) / (zc + self.RLEAK), self.RLEAK

    def _measure(self, wait):
        from .engmath import params_from_z
        period = {"SLOW": 0.5, "MED": 0.2, "FAST": 0.05}.get(self.s["speed"], 0.2)
        if wait:
            delay = period - (time.monotonic() - self._last)
            if delay > 0:
                time.sleep(delay)
        self._last = time.monotonic()
        s = self.s
        noise = lambda: 1 + random.gauss(0, 0.0004)
        f = FREQ_HZ[s["frequency"]]
        z, dcr = self._dut(2 * math.pi * f, f, float(s["level"].rstrip("V")))
        if s["primary"] == "DCR":
            return f"{dcr * noise():+.5e},{0:+.5e},N"
        p = params_from_z(z, f)
        ser = s["equivalent"] == "SER"
        prim = {
            "C": p["Cs"] if ser else p["Cp"], "L": p["Ls"] if ser else p["Lp"],
            "R": p["Rs"] if ser else p["Rp"], "Z": p["|Z|"],
        }[s["primary"]]
        sec = {
            "D": p["D"], "Q": p["Q"], "X": p["Xs"] if ser else p["Xp"], "DEG": p["θ"],
            "RAD": math.radians(p["θ"]), "ESR": p["Rs"],
        }[s["secondary"]]
        prim = (prim if prim is not None else -1e-12) * noise()
        cmp = "N"
        if s["comp"] and s["comp_nominal"]:
            cmp = "1" if abs(prim / s["comp_nominal"] - 1) * 100 <= s["comp_tol"] else "0"
        return f"{prim:+.5e},{sec * noise():+.5e},{cmp}"


def open_meter(port: str, baud: int = 9600, log=None) -> UT622E:
    if port == DEMO_PORT:
        return SimulatedUT622E(log=log)
    return UT622E(port, baud, log=log)
