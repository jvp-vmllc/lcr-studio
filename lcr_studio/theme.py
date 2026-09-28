"""Dark / light themes: Qt palette, stylesheet and plot colours."""
from __future__ import annotations

import pyqtgraph as pg
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

PALETTES = {
    "dark": dict(
        bg="#0e1117", surface="#161b24", surface2="#1d2430", border="#2a3342", text="#e7eaf0",
        muted="#8a94a8", accent="#4c8dff", accent_hover="#6aa0ff", on_accent="#ffffff",
        good="#22c55e", bad="#f25555", warn="#f5a524", grid="#2a3342",
    ),
    "light": dict(
        bg="#f3f5f9", surface="#ffffff", surface2="#eef1f6", border="#d6dce6", text="#161a22",
        muted="#5d6679", accent="#2563eb", accent_hover="#3b76f0", on_accent="#ffffff",
        good="#16a34a", bad="#dc2626", warn="#d97706", grid="#e2e6ee",
    ),
}
SERIES = ["#4c8dff", "#f5a524", "#22c55e", "#d160ff", "#f25555", "#18c1c1", "#ff8a3d", "#9aa7bd"]
UI_FONTS = ["Segoe UI", "Inter", "Noto Sans", "Ubuntu", "Cantarell", "DejaVu Sans"]
NUM_FONTS = ["Bahnschrift", "Barlow"]      # Barlow ships in assets/fonts


def pick_font(candidates) -> str:
    from PySide6.QtGui import QFontDatabase
    installed = set(QFontDatabase.families())
    return next((f for f in candidates if f in installed), candidates[-1])

QSS = """
QMainWindow, QDialog {{ background: {bg}; }}
QWidget {{ color: {text}; font-family: "{ui}"; font-size: 10pt; }}
QToolTip {{ background: {surface2}; color: {text}; border: 1px solid {border}; padding: 4px; }}

QFrame#Card {{ background: {surface}; border: 1px solid {border}; border-radius: 12px; }}
QFrame#TopBar {{ background: {surface}; border-bottom: 1px solid {border}; }}
QLabel#CardTitle {{ color: {muted}; font-size: 8pt; font-weight: 600; }}
QLabel#Muted {{ color: {muted}; }}
QLabel#AppTitle {{ font-size: 14pt; font-weight: 600; }}
QLabel#FieldLabel {{ color: {muted}; font-size: 9pt; }}

QLabel#BigValue {{ font-family: {num}; font-size: 60pt; font-weight: 500; }}
QLabel#BigUnit {{ font-family: {num}; font-size: 26pt; color: {muted}; }}
QLabel#BigType {{ font-family: {num}; font-size: 24pt; color: {accent}; font-weight: 600; }}
QLabel#MidValue {{ font-family: {num}; font-size: 28pt; }}
QLabel#MidUnit {{ font-family: {num}; font-size: 16pt; color: {muted}; }}
QLabel#MidType {{ font-family: {num}; font-size: 16pt; color: {accent}; font-weight: 600; }}
QLabel#StatValue {{ font-family: {num}; font-size: 13pt; }}
QLabel#DerivedValue {{ font-family: {num}; font-size: 12pt; }}

QLabel#Badge {{ background: {surface2}; border: 1px solid {border}; border-radius: 9px;
               padding: 2px 9px; color: {muted}; font-size: 8.5pt; font-weight: 600; }}
QLabel#Badge[kind="good"] {{ background: {good}; border-color: {good}; color: #ffffff; }}
QLabel#Badge[kind="bad"] {{ background: {bad}; border-color: {bad}; color: #ffffff; }}
QLabel#Badge[kind="warn"] {{ background: {warn}; border-color: {warn}; color: #ffffff; }}
QLabel#Badge[kind="accent"] {{ background: {accent}; border-color: {accent}; color: #ffffff; }}

QPushButton {{ background: {surface2}; border: 1px solid {border}; border-radius: 7px;
              padding: 5px 12px; min-height: 18px; }}
QPushButton:hover {{ border-color: {accent}; }}
QPushButton:pressed {{ background: {border}; }}
QPushButton:checked {{ background: {accent}; border-color: {accent}; color: {on_accent}; }}
QPushButton:disabled {{ color: {muted}; border-color: {surface2}; }}
QPushButton#Accent {{ background: {accent}; border-color: {accent}; color: {on_accent}; font-weight: 600; }}
QPushButton#Accent:hover {{ background: {accent_hover}; }}
QPushButton#Danger:hover {{ border-color: {bad}; color: {bad}; }}
QPushButton#Seg {{ padding: 5px 4px; border-radius: 6px; }}
QPushButton#Icon {{ padding: 4px 8px; }}
QToolButton {{ background: {surface2}; border: 1px solid {border}; border-radius: 7px; padding: 5px 12px; }}
QToolButton:hover {{ border-color: {accent}; }}
QToolButton:disabled {{ color: {muted}; border-color: {surface2}; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}
QMenu {{ background: {surface2}; border: 1px solid {border}; border-radius: 8px; padding: 6px; }}
QMenu::item {{ padding: 6px 22px; border-radius: 5px; }}
QMenu::item:selected {{ background: {accent}; color: {on_accent}; }}
QMenu::separator {{ height: 1px; background: {border}; margin: 4px 6px; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {{
    background: {surface2}; border: 1px solid {border}; border-radius: 7px; padding: 4px 7px;
    selection-background-color: {accent}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus {{ border-color: {accent}; }}
QLineEdit[invalid="true"] {{ border-color: {bad}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{ background: {surface2}; border: 1px solid {border}; selection-background-color: {accent}; }}

QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {muted}; padding: 9px 16px; border: none;
               border-bottom: 2px solid transparent; font-weight: 600; }}
QTabBar::tab:selected {{ color: {text}; border-bottom: 2px solid {accent}; }}
QTabBar::tab:hover {{ color: {text}; }}

QTableView, QTableWidget, QListWidget {{ background: {surface}; alternate-background-color: {surface2};
    border: 1px solid {border}; border-radius: 8px; gridline-color: {border};
    selection-background-color: {accent}; selection-color: {on_accent}; }}
QHeaderView::section {{ background: {surface2}; color: {muted}; border: none;
    border-bottom: 1px solid {border}; padding: 5px 6px; font-weight: 600; }}
QTableCornerButton::section {{ background: {surface2}; border: none; }}

QProgressBar {{ background: {surface2}; border: 1px solid {border}; border-radius: 6px; height: 12px;
               text-align: center; font-size: 8pt; }}
QProgressBar::chunk {{ background: {accent}; border-radius: 5px; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle {{ background: {border}; border-radius: 3px; min-height: 24px; min-width: 24px; }}
QScrollBar::handle:hover {{ background: {muted}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ height: 0; width: 0; background: none; }}

QCheckBox {{ spacing: 7px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {border}; background: {surface2}; }}
QCheckBox::indicator:checked {{ background: {accent}; border-color: {accent}; }}
QSplitter::handle {{ background: transparent; }}
QStatusBar {{ background: {surface}; border-top: 1px solid {border}; color: {muted}; padding-left: 12px; }}
QStatusBar::item {{ border: none; }}
QMenu {{ background: {surface}; border: 1px solid {border}; }}
QMenu::item:selected {{ background: {accent}; color: {on_accent}; }}
"""


class ThemeManager(QObject):
    changed = Signal(dict)

    def __init__(self):
        super().__init__()
        self.name = "dark"
        self.c = PALETTES["dark"]

    def apply(self, name: str):
        self.name = name if name in PALETTES else "dark"
        self.c = PALETTES[self.name]
        c = self.c
        app = QApplication.instance()
        pal = QPalette()
        for role, key in [
            (QPalette.Window, "bg"), (QPalette.WindowText, "text"), (QPalette.Base, "surface2"),
            (QPalette.AlternateBase, "surface"), (QPalette.Text, "text"), (QPalette.Button, "surface2"),
            (QPalette.ButtonText, "text"), (QPalette.Highlight, "accent"),
            (QPalette.HighlightedText, "on_accent"), (QPalette.ToolTipBase, "surface2"),
            (QPalette.ToolTipText, "text"), (QPalette.PlaceholderText, "muted"),
        ]:
            pal.setColor(role, QColor(c[key]))
        app.setPalette(pal)
        app.setStyleSheet(QSS.format(ui=pick_font(UI_FONTS), num=f'"{pick_font(NUM_FONTS)}"', **c))
        pg.setConfigOptions(antialias=True, background=c["surface"], foreground=c["muted"])
        self.changed.emit(c)


theme = ThemeManager()


def style_plot(plot: pg.PlotItem, c: dict):
    """Apply theme colours to an existing PlotItem."""
    for name in ("left", "bottom", "right"):
        ax = plot.getAxis(name)
        ax.setPen(pg.mkPen(c["border"]))
        ax.setTextPen(pg.mkPen(c["muted"]))
    plot.showGrid(x=True, y=True, alpha=0.18)


def repolish(widget):
    widget.style().unpolish(widget)
    widget.style().polish(widget)
