"""Flyback transformer test: profile, Lp / Llk steps, measurement job and evaluation.

Method (standard magnetics practice):
  * Lp  — primary inductance, every other winding open (magnetizing inductance).
  * Llk — primary inductance with every other winding shorted (leakage inductance).
  * k   — coupling coefficient, k = sqrt(1 - Llk / Lp).
  * n   — turns ratio estimate from the inductance ratio, Np/Ns ≈ sqrt(Lp / Ls).
Both inductances are measured over a frequency × test-level matrix so the report shows how the
part behaves at each drive level; limits are checked at the specification condition.
"""
from __future__ import annotations

import math
import statistics
import time
from dataclasses import asdict, dataclass, field

from .engmath import fmt
from .ut622e import FREQ_HZ, FREQUENCIES, LEVELS, MeterError

@dataclass
class FlybackProfile:
    part_number: str = ""
    lp_freq: str = "10kHz"
    lp_level: str = "1.0V"
    llk_freq: str = "10kHz"
    llk_level: str = "1.0V"
    lp_nom: float | None = None
    lp_tol: float = 10.0
    llk_max: float | None = None
    llk_pct_max: float | None = None
    lp_equ: str = "SER"
    llk_equ: str = "SER"
    freqs: list[str] = field(default_factory=lambda: list(FREQUENCIES))
    levels: list[str] = field(default_factory=lambda: list(LEVELS))
    settle: int = 2
    navg: int = 5

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "FlybackProfile":
        d = dict(d)
        for old, new in (("spec_freq", ("lp_freq", "llk_freq")), ("spec_level", ("lp_level", "llk_level"))):
            if old in d:                                    # profiles before 2.9 had one shared condition
                for k in new:
                    d.setdefault(k, d[old])
        known = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in d.items() if k in known})

    def matrix(self, freq: str, level: str):
        """Frequencies and levels to measure for a step, always including its test condition."""
        freqs = [f for f in FREQUENCIES if f in self.freqs or f == freq]
        levels = [lv for lv in LEVELS if lv in self.levels or lv == level]
        return freqs, levels


@dataclass
class Step:
    key: str                 # "lp", "llk"
    title: str
    instruction: str
    fixture: str             # simulator fixture id
    equ: str
    freqs: list[str]
    levels: list[str]


def build_steps(p: FlybackProfile) -> list[Step]:
    """Primary inductance, then leakage inductance. Per-winding secondary steps are left out for now."""
    lp_f, lp_l = p.matrix(p.lp_freq, p.lp_level)
    llk_f, llk_l = p.matrix(p.llk_freq, p.llk_level)
    return [
        Step("lp", "Primary inductance Lp",
             "Connect the meter to the primary winding. Leave every other winding open.",
             "PRI_OPEN", p.lp_equ, lp_f, lp_l),
        Step("llk", "Leakage inductance Llk",
             "Keep the meter on the primary winding. Short every other winding.",
             "PRI_SHORT", p.llk_equ, llk_f, llk_l),
    ]


def hz_label(f: str) -> str:
    return f.replace("kHz", " kHz") if "k" in f else f.replace("Hz", " Hz")


def v_label(lv: str) -> str:
    return lv.replace("V", " V")


class _Aborted(Exception):
    pass


def run_step(meter, ctx, step: Step, settle: int, navg: int) -> dict:
    """Worker-thread job: measure L and Q over the step's matrix, then restore the meter."""
    st0 = meter.read_settings(full=True)
    if st0.get("comp"):
        raise ValueError("Turn off tolerance mode on the meter first.")
    if hasattr(meter, "set_fixture"):
        meter.set_fixture(step.fixture)          # simulator only
    get = meter.fetch if st0.get("trigger") == "AUTO" else meter.trigger_fetch
    rows, aborted = [], False
    total = len(step.freqs) * len(step.levels)
    done = 0

    def take(n):
        vals = []
        for _ in range(n):
            if ctx.aborted:
                raise _Aborted
            vals.append(get())
        return vals

    try:
        meter.set_primary("L")
        meter.set_secondary("Q")
        meter.set_equivalent(step.equ)
        for lv in step.levels:
            meter.set_level(lv)
            for f in step.freqs:
                meter.set_frequency(f)
                take(settle + 1)
                vals = take(navg)
                ls = [v[0] for v in vals]
                qs = [v[1] for v in vals]
                row = {"freq": f, "hz": FREQ_HZ[f], "level": lv, "L": statistics.fmean(ls),
                       "L_sd": statistics.stdev(ls) if len(ls) > 1 else 0.0, "Q": statistics.fmean(qs)}
                rows.append(row)
                done += 1
                ctx.partial({"step": step.key, "row": row})
                ctx.progress(done, total, f"{step.title} · {v_label(lv)} · {hz_label(f)}")
    except _Aborted:
        aborted = True
    finally:
        try:
            meter.set_frequency(st0["frequency"])
            meter.set_level(st0["level"])
            meter.set_primary(st0["primary"])
            if st0.get("primary") != "DCR":
                if st0.get("secondary"):
                    meter.set_secondary(st0["secondary"])
                meter.set_equivalent(st0["equivalent"])
        except (MeterError, KeyError, TypeError):
            pass
    return {"step": step.key, "rows": rows, "aborted": aborted, "equ": step.equ, "t": time.time()}


# ------------------------------------------------------------ evaluation --

def point(result: dict | None, freq: str, level: str):
    if not result:
        return None
    return next((r for r in result["rows"] if r["freq"] == freq and r["level"] == level), None)


def esr_from(row, equ):
    if not row or not row["Q"]:
        return None
    w = 2 * math.pi * row["hz"]
    return w * row["L"] / row["Q"] if equ == "SER" else w * row["L"] * row["Q"]


def evaluate(p: FlybackProfile, results: dict) -> tuple[list[dict], str]:
    """Summary rows (param, cond, value, limit, status) and the overall verdict."""
    f, lv = p.lp_freq, p.lp_level
    cond = f"{hz_label(f)}, {v_label(lv)}"                                   # Lp test condition
    lcond = f"{hz_label(p.llk_freq)}, {v_label(p.llk_level)}"                # Llk test condition
    ratio_cond = cond if (p.llk_freq, p.llk_level) == (f, lv) else f"Lp {cond} / Llk {lcond}"
    rows = []

    def add(param, cond_, value, limit="—", status="INFO"):
        rows.append({"param": param, "cond": cond_, "value": value, "limit": limit, "status": status})

    lp_res, llk_res = results.get("lp"), results.get("llk")
    lp_pt, llk_pt = point(lp_res, f, lv), point(llk_res, p.llk_freq, p.llk_level)
    lp = lp_pt["L"] if lp_pt else None
    llk = llk_pt["L"] if llk_pt else None

    if lp is not None:
        if p.lp_nom:
            lo, hi = p.lp_nom * (1 - p.lp_tol / 100), p.lp_nom * (1 + p.lp_tol / 100)
            ok = lo <= lp <= hi
            add("Primary inductance Lp", cond + f", {p.lp_equ.lower()}", fmt(lp, "H"),
                f"{fmt(p.lp_nom, 'H', 4)} ± {p.lp_tol:g} %", "PASS" if ok else "FAIL")
            rows[-1]["value"] += f"  ({(lp / p.lp_nom - 1) * 100:+.2f} %)"
        else:
            add("Primary inductance Lp", cond + f", {p.lp_equ.lower()}", fmt(lp, "H"))
    else:
        add("Primary inductance Lp", cond, "not measured", status="—")

    if llk is not None:
        if p.llk_max:
            add("Leakage inductance Llk", lcond + f", {p.llk_equ.lower()}", fmt(llk, "H"),
                f"≤ {fmt(p.llk_max, 'H', 4)}", "PASS" if llk <= p.llk_max else "FAIL")
        else:
            add("Leakage inductance Llk", lcond + f", {p.llk_equ.lower()}", fmt(llk, "H"))
    else:
        add("Leakage inductance Llk", lcond, "not measured", status="—")

    if lp and llk is not None and lp > 0:
        pct = llk / lp * 100
        if p.llk_pct_max:
            add("Leakage ratio Llk / Lp", ratio_cond, f"{pct:.3f} %", f"≤ {p.llk_pct_max:g} %",
                "PASS" if pct <= p.llk_pct_max else "FAIL")
        else:
            add("Leakage ratio Llk / Lp", ratio_cond, f"{pct:.3f} %")
        k = math.sqrt(max(0.0, 1 - llk / lp))
        add("Coupling coefficient k", ratio_cond, f"{k:.5f}")

    if lp_pt:
        add("Primary Q", cond, f"{lp_pt['Q']:.4g}")
        esr = esr_from(lp_pt, p.lp_equ)
        if esr is not None:
            add("Primary ESR (Rs)" if p.lp_equ == "SER" else "Primary Rp", cond, fmt(esr, "Ω"))
        lvs = [r for r in lp_res["rows"] if r["freq"] == f]
        if len(lvs) > 1:
            first, last = lvs[0], lvs[-1]
            add("Lp level dependence", f"{hz_label(f)}, {v_label(first['level'])} → {v_label(last['level'])}",
                f"{(last['L'] / first['L'] - 1) * 100:+.3f} %")

    statuses = [r["status"] for r in rows]
    required = ["lp", "llk"]
    complete = all(k in results and not results[k].get("aborted") for k in required)
    if "FAIL" in statuses:
        verdict = "FAIL"
    elif not complete:
        verdict = "INCOMPLETE"
    elif "PASS" in statuses:
        verdict = "PASS"
    else:
        verdict = "MEASURED"
    return rows, verdict
