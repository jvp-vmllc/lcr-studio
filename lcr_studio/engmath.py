"""Engineering-notation formatting and impedance math."""
from __future__ import annotations

import math
import re

_PREFIX = {12: "T", 9: "G", 6: "M", 3: "k", 0: "", -3: "m", -6: "µ", -9: "n", -12: "p", -15: "f"}
_PARSE_PREFIX = {"T": 1e12, "G": 1e9, "M": 1e6, "k": 1e3, "K": 1e3, "m": 1e-3, "u": 1e-6,
                 "µ": 1e-6, "μ": 1e-6, "n": 1e-9, "N": 1e-9, "p": 1e-12, "P": 1e-12, "f": 1e-15}


def eng_parts(value, digits: int = 5):
    """Split a value into (mantissa text, SI prefix) with `digits` significant digits."""
    if value is None or isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return ("—" if value is None or math.isnan(value) else ("∞" if value > 0 else "-∞")), ""
    if value == 0:
        return f"{0:.{max(digits - 1, 0)}f}", ""
    exp3 = int(math.floor(math.log10(abs(value)) / 3) * 3)
    exp3 = max(-15, min(12, exp3))
    for _ in range(2):
        m = float(f"{value / 10 ** exp3:.{digits}g}")   # round to significant digits first
        if abs(m) >= 1000 and exp3 < 12:
            exp3 += 3
            continue
        break
    decimals = max(0, digits - 1 - int(math.floor(math.log10(abs(m))))) if m else digits - 1
    return f"{m:.{decimals}f}", _PREFIX[exp3]


def fmt(value, unit: str = "", digits: int = 5) -> str:
    m, p = eng_parts(value, digits)
    return f"{m} {p}{unit}".rstrip()


def parse_eng(text: str) -> float:
    """Parse '4.7u', '4.7 µF', '10k', '1e-6', '2.2nF', '100m' (milli), '1M' (mega)."""
    t = text.strip().replace(",", "")
    m = re.fullmatch(r"([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*([TGMkKmuµμnNpPf]?)\s*[A-Za-zΩ°]*", t)
    if not m:
        raise ValueError(f"Cannot parse {text!r}")
    return float(m.group(1)) * _PARSE_PREFIX.get(m.group(2), 1.0)


# ---------------------------------------------------------------- impedance --

def params_from_z(z: complex, f: float) -> dict:
    """Every common equivalent-circuit parameter derived from complex impedance z at f."""
    w = 2 * math.pi * f
    rs, xs = z.real, z.imag
    y = 1 / z if z != 0 else complex(math.inf, 0)
    g, b = y.real, y.imag
    d = abs(rs / xs) if xs else math.inf
    return {
        "|Z|": abs(z),
        "θ": math.degrees(math.atan2(xs, rs)),
        "Rs": rs,
        "Xs": xs,
        "Rp": 1 / g if g else math.inf,
        "Xp": -1 / b if b else math.inf,
        "Cs": -1 / (w * xs) if xs < 0 else None,
        "Cp": b / w if b > 0 else None,
        "Ls": xs / w if xs > 0 else None,
        "Lp": -1 / (w * b) if b < 0 else None,
        "D": d,
        "Q": 1 / d if d else math.inf,
        "G": g,
        "B": b,
    }


def z_from_lc(kind: str, value: float, d: float, f: float, series: bool) -> complex | None:
    """Impedance of a C or L of `value` with dissipation factor d, series or parallel model."""
    if value <= 0 or f <= 0 or d < 0:
        return None
    w = 2 * math.pi * f
    if series:
        x = -1 / (w * value) if kind == "C" else w * value
        return complex(d * abs(x), x)
    bsus = w * value if kind == "C" else -1 / (w * value)
    return 1 / complex(abs(bsus) * d, bsus)


def z_from_measurement(ptype, pval, stype, sval, f, equ) -> complex | None:
    """Reconstruct complex impedance from a meter reading, or None if under-determined."""
    if f is None or ptype == "DCR" or stype is None or pval is None or sval is None:
        return None
    series = equ != "PAR"
    theta = math.radians(sval) if stype == "DEG" else sval if stype == "RAD" else None
    try:
        if ptype in ("C", "L"):
            if stype == "D":
                d = sval
            elif stype == "Q":
                d = 1 / sval
            elif theta is not None:
                d = abs(math.cos(theta) / math.sin(theta))
            elif stype == "ESR":
                w = 2 * math.pi * f
                x = 1 / (w * pval) if ptype == "C" else w * pval
                d = sval / x
            else:
                return None
            return z_from_lc(ptype, pval, abs(d), f, series)
        if ptype == "R":
            if stype == "X":
                return complex(pval, sval) if series else 1 / complex(1 / pval, -1 / sval if sval else 0)
            if theta is not None:
                if series:
                    return complex(pval, pval * math.tan(theta))
                gg = 1 / pval
                return 1 / complex(gg, -gg * math.tan(theta))
            return None
        if ptype == "Z":
            if theta is not None:
                return pval * complex(math.cos(theta), math.sin(theta))
            if stype == "X":
                return complex(math.sqrt(max(pval ** 2 - sval ** 2, 0.0)), sval)
            return None
    except (ZeroDivisionError, ValueError, OverflowError):
        return None
    return None


# ------------------------------------------------------------ E-series --

E_SERIES = {
    "E6": [10, 15, 22, 33, 47, 68],
    "E12": [10, 12, 15, 18, 22, 27, 33, 39, 47, 56, 68, 82],
    "E24": [10, 11, 12, 13, 15, 16, 18, 20, 22, 24, 27, 30, 33, 36, 39, 43, 47, 51, 56, 62, 68, 75, 82, 91],
    "E48": [100, 105, 110, 115, 121, 127, 133, 140, 147, 154, 162, 169, 178, 187, 196, 205, 215, 226,
            237, 249, 261, 274, 287, 301, 316, 332, 348, 365, 383, 402, 422, 442, 464, 487, 511, 536,
            562, 590, 619, 649, 681, 715, 750, 787, 825, 866, 909, 953],
    "E96": [100, 102, 105, 107, 110, 113, 115, 118, 121, 124, 127, 130, 133, 137, 140, 143, 147, 150,
            154, 158, 162, 165, 169, 174, 178, 182, 187, 191, 196, 200, 205, 210, 215, 221, 226, 232,
            237, 243, 249, 255, 261, 267, 274, 280, 287, 294, 301, 309, 316, 324, 332, 340, 348, 357,
            365, 374, 383, 392, 402, 412, 422, 432, 442, 453, 464, 475, 487, 499, 511, 523, 536, 549,
            562, 576, 590, 604, 619, 634, 649, 665, 681, 698, 715, 732, 750, 768, 787, 806, 825, 845,
            866, 887, 909, 931, 953, 976],
}


def nearest_standard(value: float, series: str = "E24") -> float | None:
    if value is None or value <= 0:
        return None
    base = E_SERIES[series]
    scale = 10 if base[0] == 10 else 100
    decade = math.floor(math.log10(value))
    best = None
    for dec in (decade - 1, decade, decade + 1):
        for v in base:
            cand = v / scale * 10 ** dec
            if best is None or abs(math.log(cand / value)) < abs(math.log(best / value)):
                best = cand
    return float(f"{best:.6g}")
