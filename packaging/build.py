"""Build a release package for the current OS.

    uv run --group build python packaging/build.py

Windows -> dist/LCR-Studio-<version>-windows-x64.exe  (single file)
Linux   -> dist/lcr-studio_<version>_amd64.deb         (installs to /opt/lcr-studio)
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "packaging"
DIST = ROOT / "dist"
VERSION = re.search(r'__version__\s*=\s*"([^"]+)"', (ROOT / "lcr_studio" / "__init__.py").read_text()).group(1)

# Qt libraries the xcb platform plugin loads from the system (PyInstaller bundles Qt itself).
DEB_DEPENDS = ", ".join([
    "libxcb-cursor0", "libxkbcommon-x11-0", "libxcb-icccm4", "libxcb-image0", "libxcb-keysyms1",
    "libxcb-randr0", "libxcb-render-util0", "libxcb-shape0", "libxcb-xinerama0", "libxcb-xkb1",
    "libegl1", "libgl1", "libfontconfig1", "libdbus-1-3",
])


def pyinstaller(name: str, onefile: bool, icon: Path | None):
    import PyInstaller.__main__
    args = [
        str(PKG / "entry.py"), "--name", name, "--noconfirm", "--clean", "--windowed",
        "--distpath", str(DIST), "--workpath", str(ROOT / "build"), "--specpath", str(ROOT / "build"),
        "--add-data", f"{ROOT / 'lcr_studio' / 'assets'}{os.pathsep}lcr_studio/assets",
        "--collect-submodules", "lcr_studio",
        # Qt modules the app never uses — keeps the package small.
        *[x for m in ("QtWebEngineCore", "QtWebEngineWidgets", "QtQuick", "QtQml", "Qt3DCore", "QtMultimedia",
                      "QtPdf", "QtCharts", "QtDataVisualization", "QtNetwork", "QtSql", "QtTest")
          for x in ("--exclude-module", f"PySide6.{m}")],
        "--exclude-module", "tkinter", "--exclude-module", "matplotlib",
        "--onefile" if onefile else "--onedir",
    ]
    if icon:
        args += ["--icon", str(icon)]
    PyInstaller.__main__.run(args)


def build_windows() -> Path:
    pyinstaller("LCR-Studio", onefile=True, icon=PKG / "icon.ico")
    out = DIST / f"LCR-Studio-{VERSION}-windows-x64.exe"
    out.unlink(missing_ok=True)
    (DIST / "LCR-Studio.exe").rename(out)
    return out


def build_deb() -> Path:
    pyinstaller("lcr-studio", onefile=False, icon=None)
    arch = subprocess.run(["dpkg", "--print-architecture"], capture_output=True, text=True, check=True).stdout.strip()
    root = ROOT / "build" / "deb"
    shutil.rmtree(root, ignore_errors=True)
    shutil.copytree(DIST / "lcr-studio", root / "opt" / "lcr-studio")
    (root / "usr" / "bin").mkdir(parents=True)
    launcher = root / "usr" / "bin" / "lcr-studio"
    launcher.write_text('#!/bin/sh\nexec /opt/lcr-studio/lcr-studio "$@"\n')
    launcher.chmod(0o755)
    apps = root / "usr" / "share" / "applications"
    apps.mkdir(parents=True)
    shutil.copy(PKG / "linux" / "lcr-studio.desktop", apps)
    for size in (48, 128, 256, 512):
        d = root / "usr" / "share" / "icons" / "hicolor" / f"{size}x{size}" / "apps"
        d.mkdir(parents=True)
        from PIL import Image
        Image.open(ROOT / "lcr_studio" / "assets" / "icon.png").resize((size, size), Image.LANCZOS).save(d / "lcr-studio.png")
    rules = root / "usr" / "lib" / "udev" / "rules.d"
    rules.mkdir(parents=True)
    shutil.copy(PKG / "linux" / "60-lcr-studio.rules", rules)
    doc = root / "usr" / "share" / "doc" / "lcr-studio"
    doc.mkdir(parents=True)
    shutil.copy(ROOT / "lcr_studio" / "assets" / "fonts" / "OFL.txt", doc / "Barlow-OFL.txt")
    shutil.copy(ROOT / "LICENSE", doc / "copyright")

    size_kb = sum(f.stat().st_size for f in root.rglob("*") if f.is_file() and not f.is_symlink()) // 1024
    debian = root / "DEBIAN"
    debian.mkdir()
    (debian / "control").write_text(f"""Package: lcr-studio
Version: {VERSION}
Section: electronics
Priority: optional
Architecture: {arch}
Installed-Size: {size_kb}
Depends: {DEB_DEPENDS}
Maintainer: {os.environ.get("DEB_MAINTAINER", "LCR Studio <lcr-studio@users.noreply.github.com>")}
Homepage: {os.environ.get("DEB_HOMEPAGE", "https://github.com/jvrpapa05/lcr-studio")}
Description: Desktop application for the UNI-T UT622E LCR meter
 Live readout with derived equivalent-circuit parameters, frequency/level
 sweeps, component sorting and matching, data logging with Excel export,
 engineering calculators and a raw SCPI console.
""")
    for script, body in {
        "postinst": "udevadm control --reload-rules 2>/dev/null || true\n"
                    "udevadm trigger --subsystem-match=tty 2>/dev/null || true\n"
                    "command -v update-desktop-database >/dev/null && update-desktop-database -q || true\n",
        "postrm": "udevadm control --reload-rules 2>/dev/null || true\n",
    }.items():
        p = debian / script
        p.write_text("#!/bin/sh\nset -e\n" + body)
        p.chmod(0o755)
    out = DIST / f"lcr-studio_{VERSION}_{arch}.deb"
    subprocess.run(["dpkg-deb", "--build", "--root-owner-group", str(root), str(out)], check=True)
    return out


if __name__ == "__main__":
    system = platform.system()
    if system == "Windows":
        path = build_windows()
    elif system == "Linux":
        path = build_deb()
    else:
        sys.exit(f"Unsupported platform: {system}")
    print(f"built {path} ({path.stat().st_size / 1e6:.1f} MB)")
