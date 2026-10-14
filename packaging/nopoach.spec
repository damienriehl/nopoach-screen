# PyInstaller one-folder build. Build on each target OS; no cross-compilation.
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH).parent
# Include application YAML, templates, local scripts and fonts. __file__ remains
# relative to the collected package so existing asset lookups continue to work.
datas, binaries, hiddenimports = collect_all("nopoach")
# Statistical fitting loads modules and metadata dynamically.
for package in ("pyfixest", "splink"):
    extra_data, extra_binaries, extra_imports = collect_all(package)
    datas += extra_data
    binaries += extra_binaries
    hiddenimports += extra_imports

a = Analysis(
    [str(root / "packaging" / "launcher.py")],
    pathex=[str(root / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["pytest", "playwright", "axe_playwright_python", "mypy", "ruff"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="nopoach-screen",
    console=True,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="nopoach-screen")
