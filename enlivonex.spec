# PyInstaller one-folder desktop distribution. User data and credentials are
# intentionally not bundled; the app creates them beside the executable.
from PyInstaller.building.datastruct import TOC

a = Analysis(
    ["run_enlivonex.py"],
    pathex=["."],
    binaries=[],
    datas=[
        ("app/assets/enlivonex.ico", "app/assets"),
        ("app/assets/enlivonex_mark.png", "app/assets"),
        ("app/assets/enlivonex_wordmark.png", "app/assets"),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "tests"],
    noarchive=False,
)

# PyInstaller's dependency scan can discover API-set forwarder DLLs from
# unrelated directories on the build machine's PATH (for example native
# libraries installed by developer tools). These are Windows system API
# contracts, not application DLLs. Shipping them beside the executable makes
# Windows load the copied forwarders instead of resolving the contracts using
# the target PC's system DLLs, which can break QtCore imports on other PCs.
_windows_api_contract_prefixes = ("api-ms-win-", "ext-ms-win-")
# The build machine's developer-tools PATH also exposes its own UCRT and ICU
# runtime. That ICU 78 build exports version-suffixed symbols, while QtCore
# imports the Windows ICU's unsuffixed symbols. Let Windows use its system
# runtimes instead of bundling those unrelated copies.
_unrelated_system_runtime_dlls = {"ucrtbase.dll", "icuuc.dll", "icudt78.dll"}
a.binaries = TOC(
    entry
    for entry in a.binaries
    if not entry[0].lower().startswith(_windows_api_contract_prefixes)
    and entry[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
    not in _unrelated_system_runtime_dlls
)

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Enlivonex",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    icon="app/assets/enlivonex.ico",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Enlivonex-Windows",
)
