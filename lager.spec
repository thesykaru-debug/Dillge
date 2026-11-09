# PyInstaller-Bauplan (Windows: DillgeLager.exe, Mac: DillgeLager-Mac)
import os
import sys

name = os.environ.get("LAGER_BUILD_NAME", "DillgeLager")
a = Analysis(["lager_start.py"], datas=[("templates", "templates"), ("static", "static")],
             hiddenimports=["app", "db"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name=name, console=True, upx=False)
