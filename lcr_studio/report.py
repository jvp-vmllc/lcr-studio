"""One-page US Letter flyback transformer test report (vector PDF + on-screen preview)."""
from __future__ import annotations

import math
import platform
from datetime import datetime

from PySide6.QtCore import QMarginsF, QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QFontMetricsF, QImage, QPageLayout, QPageSize, QPainter, QPainterPath, QPdfWriter,
                           QPen)

from .engmath import eng_parts, fmt
from .flyback import FlybackProfile, esr_from, evaluate, hz_label, point, v_label
from .theme import UI_FONTS, pick_font
from .ut622e import FREQ_HZ, FREQUENCIES, LEVELS

PAGE_W, PAGE_H = 612.0, 792.0          # US Letter in points
MARGIN = 34.0
INK, MUTED, FAINT, GRID = "#14171f", "#5b6475", "#9aa3b2", "#e3e7ee"
ACCENT, GOOD, BAD, WARN = "#2563eb", "#16a34a", "#dc2626", "#d97706"
LEVEL_COLORS = {"0.1V": "#2563eb", "0.3V": "#f59e0b", "1.0V": "#16a34a"}
VERDICT_COLOR = {"PASS": GOOD, "FAIL": BAD, "INCOMPLETE": WARN, "MEASURED": ACCENT}


class Canvas:
    """Draw in points regardless of device resolution."""

    def __init__(self, painter: QPainter, dpi: float):
        self.p = painter
        self.s = dpi / 72.0
        self.family = pick_font(UI_FONTS)

    def r(self, x, y, w, h):
        s = self.s
        return QRectF(x * s, y * s, w * s, h * s)

    def font(self, size, bold=False):
        f = QFont(self.family)
        f.setPointSizeF(size)
        f.setBold(bold)
        return f

    def text(self, x, y, w, h, text, size=8, bold=False, color=INK, align=Qt.AlignLeft | Qt.AlignVCenter,
             elide=False):
        f = self.font(size, bold)
        self.p.setFont(f)
        self.p.setPen(QColor(color))
        rect = self.r(x, y, w, h)
        if elide:
            text = QFontMetricsF(f, self.p.device()).elidedText(text, Qt.ElideRight, rect.width())
        self.p.drawText(rect, int(align), text)

    def line(self, x1, y1, x2, y2, color=GRID, width=0.6, style=Qt.SolidLine):
        s = self.s
        self.p.setPen(QPen(QColor(color), width * s, style, Qt.RoundCap))
        self.p.drawLine(QPointF(x1 * s, y1 * s), QPointF(x2 * s, y2 * s))

    def box(self, x, y, w, h, fill=None, stroke=None, radius=0.0, width=0.6):
        self.p.setPen(QPen(QColor(stroke), width * self.s) if stroke else Qt.NoPen)
        self.p.setBrush(QColor(fill) if fill else Qt.NoBrush)
        self.p.drawRoundedRect(self.r(x, y, w, h), radius * self.s, radius * self.s)
        self.p.setBrush(Qt.NoBrush)

    def polyline(self, pts, color, width=1.3, dots=True):
        s = self.s
        path = QPainterPath()
        for i, (x, y) in enumerate(pts):
            (path.moveTo if i == 0 else path.lineTo)(x * s, y * s)
        self.p.setPen(QPen(QColor(color), width * s, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        self.p.drawPath(path)
        if dots:
            self.p.setBrush(QColor(color))
            self.p.setPen(Qt.NoPen)
            for x, y in pts:
                self.p.drawEllipse(QPointF(x * s, y * s), 1.7 * s, 1.7 * s)
            self.p.setBrush(Qt.NoBrush)


def nice_ticks(lo, hi, n=5):
    if hi <= lo:
        hi, lo = lo + abs(lo) * 0.01 + 1e-15, lo - abs(lo) * 0.01 - 1e-15
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    first, last = math.floor(lo / step + 1e-9), math.ceil(hi / step - 1e-9)
    return [k * step for k in range(first, last + 1)], step


def tick_label(v, step, unit):
    dec0 = max(0, -math.floor(math.log10(step) + 1e-9))
    if abs(v) < step * 1e-6:
        v = 0.0
    if unit == "%":
        return ("+" if v > 0 else "") + f"{v:.{dec0}f} %"
    if not unit:
        return f"{v:.{dec0}f}"
    m, pre = eng_parts(max(abs(v), step), 3)
    scale = max(abs(v), step) / float(m) if float(m) else 1
    dec = max(0, -math.floor(math.log10(step / scale) + 1e-9))
    return f"{v / scale:.{dec}f} {pre}{unit}"


def chart(cv: Canvas, x, y, w, h, title, series, unit, xs_kind="freq", mark=None):
    """series: [(label, color, [(x, y), ...])]. xs_kind 'freq' uses a log axis, 'level' a category axis.

    The y axis turns logarithmic when the data spans more than a decade. mark=(x, y, x label, y label) draws a
    dashed cross-hair through one point with its labels.
    """
    cv.text(x, y, w, 12, title, 8.2, True)
    px, py, pw, ph = x + 44, y + 16, w - 48, h - 36
    cv.box(px, py, pw, ph, fill="#fbfcfe", stroke=GRID)
    pts_all = [pt for _, _, pts in series for pt in pts if pt[1] is not None and math.isfinite(pt[1])]
    if not pts_all:
        cv.text(px, py, pw, ph, "no data", 8, color=FAINT, align=Qt.AlignCenter)
        return
    ys = [v for _, v in pts_all]
    lo, hi = min(ys), max(ys)
    logy = lo > 0 and hi / lo > 10
    if logy:
        y0, y1 = math.log10(lo) - 0.08, math.log10(hi) + 0.08
        ticks = [m * 10 ** d for d in range(math.floor(y0), math.ceil(y1) + 1) for m in (1, 2, 5)
                 if y0 <= math.log10(m * 10 ** d) <= y1]
        step = None
    else:
        pad = (hi - lo) * 0.12 or abs(hi) * 0.02 or 1e-12
        bottom = 0.0 if lo >= 0 > lo - pad else lo - pad
        ticks, step = nice_ticks(bottom, hi + pad, 4)
        y0, y1 = ticks[0], ticks[-1]

    if xs_kind == "freq":
        xl0, xl1 = math.log10(80), math.log10(125e3)
        xmap = lambda v: px + (math.log10(v) - xl0) / (xl1 - xl0) * pw
        xticks = [(FREQ_HZ[f], f.replace("Hz", "")) for f in FREQUENCIES if f != "120Hz"]
    else:
        xmap = lambda v: px + (LEVELS.index(v) + 0.5) / len(LEVELS) * pw
        xticks = [(lv, v_label(lv)) for lv in LEVELS]
    ymap = lambda v: py + ph - ((math.log10(v) if logy else v) - y0) / (y1 - y0) * ph

    for t in ticks:
        yy = ymap(t)
        cv.line(px, yy, px + pw, yy, GRID, 0.4)
        cv.text(x, yy - 5, 41, 10, fmt(t, unit, 2) if logy else tick_label(t, step, unit), 6.1, color=MUTED,
                align=Qt.AlignRight | Qt.AlignVCenter)
    for xv, lab in xticks:
        xx = xmap(xv)
        cv.line(xx, py, xx, py + ph, GRID, 0.4)
        cv.text(xx - 20, py + ph + 1, 40, 10, lab, 6.3, color=MUTED, align=Qt.AlignCenter)
    if xs_kind == "freq":
        cv.text(px, py + ph + 9, pw, 10, "Frequency (Hz)", 6.3, color=MUTED, align=Qt.AlignCenter)
    lx = x + w
    for label, color, pts in reversed(series):
        good = [(xmap(a), ymap(b)) for a, b in pts if b is not None and math.isfinite(b)]
        if good:
            cv.polyline(good, color)
        tw = len(label) * 3.4 + 11
        cv.line(lx - tw, y + 6, lx - tw + 7, y + 6, color, 1.8)
        cv.text(lx - tw + 9, y + 1, tw, 10, label, 6.3, color=MUTED)
        lx -= tw + 5
    if mark is not None and mark[1] is not None and mark[1] > 0:     # cross-hair on the test-condition point
        mx, my = xmap(mark[0]), ymap(mark[1])
        cv.line(mx, py, mx, py + ph, INK, 0.7, Qt.DashLine)
        cv.line(px, my, px + pw, my, INK, 0.7, Qt.DashLine)
        right = mx > px + pw * 0.6
        lx_ = mx - 65 if right else mx + 3
        cv.box(lx_, py + 3, 62, 9, fill="#ddffffff")
        cv.text(lx_, py + 3, 62, 9, mark[2], 6, True, INK, (Qt.AlignRight if right else Qt.AlignLeft) | Qt.AlignVCenter)
        ly_ = my + 2 if my < py + 13 else my - 11
        cv.box(px + 3, ly_, 46, 9, fill="#ddffffff")
        cv.text(px + 3, ly_, 46, 9, mark[3], 6, True, INK, Qt.AlignLeft | Qt.AlignVCenter)


def loss_figures(row, equ):
    """D, phase angle (degrees) and series/parallel resistance derived from the measured L and Q."""
    q = row.get("Q")
    if q is None:
        return None, None, None
    return (1 / q if q else None), math.degrees(math.atan(q)), esr_from(row, equ)


def points_table(cv, x, y, w, title, result, equ, target=None):
    """Every measured point of a step with the loss figures derived from Q; the target=(freq, level) row is
    highlighted. Returns the y below the table."""
    cv.text(x, y, w, 11, title, 7.6, True)
    y += 12
    if not result or not result["rows"]:
        cv.text(x, y, w, 12, "not measured", 7, color=FAINT)
        return y + 12
    names = ["Level", "Frequency", "Inductance", "Dissipation", "Quality factor", "Phase",
             "Series resistance" if equ == "SER" else "Parallel resistance"]
    aligns = [Qt.AlignLeft] + [Qt.AlignRight] * 6
    rows = sorted(result["rows"], key=lambda r: (LEVELS.index(r["level"]), r["hz"]))
    table = []
    for row in rows:
        d, theta, res = loss_figures(row, equ)
        table.append([v_label(row["level"]), hz_label(row["freq"]), fmt(row["L"], "H"),
                      f"{d:.4f}" if d is not None else "—", f"{row['Q']:.4g}" if row.get("Q") is not None else "—",
                      f"{theta:.2f}°" if theta is not None else "—", fmt(res, "Ω") if res is not None else "—"])
    # Column widths come from the text itself, so no header or value is clipped whatever font is installed;
    # the header shrinks a step if the full names would not fit the table width.
    cm = QFontMetricsF(cv.font(6.4, True), cv.p.device())
    for hs in (5.8, 5.4, 5.0):
        hm = QFontMetricsF(cv.font(hs, True), cv.p.device())
        need = [max([hm.horizontalAdvance(n)] + [cm.horizontalAdvance(r[i]) for r in table]) / cv.s + 7
                for i, n in enumerate(names)]
        if sum(need) <= w:
            break
    widths = [n * w / sum(need) for n in need]
    cv.box(x, y, w, 11, fill="#eef2f8")
    cx = x
    for name, cw, align in zip(names, widths, aligns):
        cv.text(cx + 3, y, cw - 6, 11, name, hs, True, MUTED, align | Qt.AlignVCenter)
        cx += cw
    y += 11
    for row, cells in zip(rows, table):
        hit = target is not None and (row["freq"], row["level"]) == tuple(target)
        if hit:                                      # the point the limit is checked at
            cv.box(x, y, w, 10.5, fill="#dbe7ff")
        cx = x
        for text, cw, align in zip(cells, widths, aligns):
            cv.text(cx + 3, y, cw - 6, 10.5, text, 6.4, hit, INK, align | Qt.AlignVCenter)
            cx += cw
        cv.line(x, y + 10.5, x + w, y + 10.5, GRID, 0.4)
        y += 10.5
    return y


def paint_report(painter: QPainter, dpi: float, profile: FlybackProfile, results: dict, meta: dict):
    cv = Canvas(painter, dpi)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.TextAntialiasing)
    painter.fillRect(cv.r(0, 0, PAGE_W, PAGE_H), QColor("#ffffff"))
    rows, verdict = evaluate(profile, results)
    x0, W = MARGIN, PAGE_W - 2 * MARGIN
    y = MARGIN

    # --- header
    cv.box(x0, y, 5, 40, fill=ACCENT, radius=1.5)
    cv.text(x0 + 13, y - 1, 380, 22, "Flyback Transformer Test Report", 17, True)
    cv.text(x0 + 13, y + 21, 380, 16, profile.part_number or "Unnamed part", 10, color=MUTED)
    vc = VERDICT_COLOR.get(verdict, ACCENT)
    cv.box(x0 + W - 118, y + 1, 118, 38, fill=vc, radius=6)
    cv.text(x0 + W - 118, y + 1, 118, 38, verdict, 17 if len(verdict) < 6 else 12, True, "#ffffff", Qt.AlignCenter)
    y += 50

    # --- identification grid
    info = [
        ("Part number", profile.part_number or "—"), ("Station", meta.get("station") or "—"),
        ("Date", meta.get("date", "")),
        ("Instrument", meta.get("meter", "—")),
        ("Primary inductance condition", f"{hz_label(profile.lp_freq)}, {v_label(profile.lp_level)} rms"),
        ("Leakage inductance condition", f"{hz_label(profile.llk_freq)}, {v_label(profile.llk_level)} rms"),
    ]
    cw = W / 3
    cv.box(x0, y, W, 44, fill="#f6f8fb", stroke=GRID, radius=4)
    for i, (k, v) in enumerate(info):
        cx, cy = x0 + (i % 3) * cw + 8, y + 4 + (i // 3) * 20
        cv.text(cx, cy, cw - 10, 8, k.upper(), 5.6, True, FAINT)
        cv.text(cx, cy + 7.5, cw - 12, 11, v, 7.2, color=INK, elide=True)
    y += 52

    # --- results summary
    cols = [("Parameter", 0.30), ("Condition", 0.24), ("Measured", 0.20), ("Limit", 0.16), ("Result", 0.10)]
    avail = 792 - MARGIN - 44 - 350 - 90   # leave room for the charts, the point tables and the footer
    rh = max(9.0, min(12.5, (avail - 12) / max(1, len(rows))))
    cv.box(x0, y, W, 12, fill="#eef2f8")
    cx = x0
    for name, frac in cols:
        cv.text(cx + 4, y, W * frac - 6, 12, name.upper(), 6, True, MUTED)
        cx += W * frac
    y += 12
    fs = 7.2 if rh >= 11 else 6.6
    for i, r in enumerate(rows):
        if i % 2:
            cv.box(x0, y, W, rh, fill="#fafbfd")
        cx = x0
        vals = [r["param"], r["cond"], r["value"], r["limit"]]
        for (name, frac), v in zip(cols[:4], vals):
            cv.text(cx + 4, y, W * frac - 6, rh, v, fs, name == "Parameter",
                    INK if name != "Condition" else MUTED, elide=True)
            cx += W * frac
        st = r["status"]
        if st in ("PASS", "FAIL"):
            cv.box(cx + 4, y + rh / 2 - 4.5, 34, 9, fill=GOOD if st == "PASS" else BAD, radius=2)
            cv.text(cx + 4, y + rh / 2 - 4.5, 34, 9, st, 5.8, True, "#ffffff", Qt.AlignCenter)
        else:
            cv.text(cx + 4, y, W * 0.1, rh, st if st != "INFO" else "info", 6.2, color=FAINT)
        y += rh
    cv.line(x0, y, x0 + W, y, GRID, 0.6)
    y += 10

    # --- charts 2 x 2
    lp_res, llk_res = results.get("lp"), results.get("llk")
    gap = 14
    chw, chh = (W - gap) / 2, 150

    def per_level(res, key="L", equ=None):
        out = []
        if not res:
            return out
        for lv in LEVELS:
            pts = []
            for r in res["rows"]:
                if r["level"] == lv:
                    val = r[key] if key != "ESR" else esr_from(r, equ)
                    pts.append((r["hz"], val))
            if pts:
                out.append((v_label(lv), LEVEL_COLORS[lv], sorted(pts)))
        return out

    def spec_mark(res, freq, level):
        pt = point(res, freq, level)
        return (pt["hz"], pt["L"], f"{hz_label(freq)}, {v_label(level)}", fmt(pt["L"], "H", 4)) if pt else None

    chart(cv, x0, y, chw, chh, "Primary inductance", per_level(lp_res), "H",
          mark=spec_mark(lp_res, profile.lp_freq, profile.lp_level))
    chart(cv, x0 + chw + gap, y, chw, chh, "Leakage inductance", per_level(llk_res), "H",
          mark=spec_mark(llk_res, profile.llk_freq, profile.llk_level))
    y += chh + 8

    # --- every measured point, with D, Q, phase and resistance derived from Q
    y_lp = points_table(cv, x0, y, chw, "Primary inductance points", lp_res, profile.lp_equ,
                        (profile.lp_freq, profile.lp_level))
    y_llk = points_table(cv, x0 + chw + gap, y, chw, "Leakage inductance points", llk_res, profile.llk_equ,
                         (profile.llk_freq, profile.llk_level))
    y = max(y_lp, y_llk) + 8

    # --- footer
    fy = PAGE_H - MARGIN - 40
    notes = meta.get("notes") or ""
    if notes:
        cv.text(x0, max(y, fy - 14), W, 12, f"Notes: {notes}", 7, color=MUTED)
    cv.line(x0, fy, x0 + W, fy, GRID, 0.6)
    for i, lab in enumerate(("Tested by", "Reviewed by", "Date")):
        sx = x0 + i * (W / 3)
        cv.line(sx, fy + 24, sx + W / 3 - 18, fy + 24, FAINT, 0.5)
        cv.text(sx, fy + 25, W / 3, 9, lab, 6.2, color=MUTED)
    cv.text(x0, PAGE_H - MARGIN - 6, W, 9,
            f"Method: primary inductance with all other windings open; leakage inductance with all other windings shorted; "
            f"k = √(1 − Llk/Lp).   "
            f"Generated by LCR Studio v{meta.get('app_version', '')}",
            5.6, color=FAINT)
    return verdict


def export_pdf(path: str, profile, results, meta) -> str:
    writer = QPdfWriter(path)
    writer.setPageLayout(QPageLayout(QPageSize(QPageSize.Letter), QPageLayout.Portrait, QMarginsF(0, 0, 0, 0)))
    writer.setResolution(300)
    writer.setTitle(f"Flyback test report {profile.part_number}".strip())
    writer.setCreator("LCR Studio")
    painter = QPainter(writer)
    try:
        return paint_report(painter, 300, profile, results, meta)
    finally:
        painter.end()


def render_image(profile, results, meta, dpi: float = 110) -> QImage:
    img = QImage(int(PAGE_W / 72 * dpi), int(PAGE_H / 72 * dpi), QImage.Format_ARGB32)
    img.setDotsPerMeterX(int(dpi / 0.0254))
    img.setDotsPerMeterY(int(dpi / 0.0254))
    img.fill(QColor("#ffffff"))
    painter = QPainter(img)
    try:
        paint_report(painter, dpi, profile, results, meta)
    finally:
        painter.end()
    return img


def default_meta(**kw) -> dict:
    meta = {"date": datetime.now().strftime("%Y-%m-%d %H:%M"), "station": platform.node(), "notes": "",
            "meter": "—", "speed": "?", "correction": "—", "app_version": ""}
    meta.update({k: v for k, v in kw.items() if v is not None})
    return meta
