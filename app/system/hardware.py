"""Collect a stable snapshot of the current PC."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import asdict, dataclass
from typing import Any, Optional

from app.utils.process import hidden_process_creation_flags


@dataclass
class HardwareInfo:
    hostname: str
    machine_id: str
    os_name: str
    os_release: str
    architecture: str
    cpu_name: str
    cpu_cores: int
    ram_gb: float
    disk_free_gb: float
    gpu_name: str
    vram_gb: float
    has_nvidia: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def collect_hardware(root: str | os.PathLike[str] = ".") -> HardwareInfo:
    """Detect CPU, RAM, disk, and GPU details for model recommendations."""
    ram_gb = _total_ram_gb()
    usage = shutil.disk_usage(root)
    gpu_name, vram_gb, has_nvidia = _detect_gpu()
    return HardwareInfo(
        hostname=platform.node() or "unknown-pc",
        machine_id=_machine_id(),
        os_name=platform.system() or "Unknown",
        os_release=platform.release() or "",
        architecture=platform.machine() or "",
        cpu_name=_cpu_name(),
        cpu_cores=os.cpu_count() or 1,
        ram_gb=round(ram_gb, 1),
        disk_free_gb=round(usage.free / (1024 ** 3), 1),
        gpu_name=gpu_name,
        vram_gb=round(vram_gb, 1),
        has_nvidia=has_nvidia,
    )


def _machine_id() -> str:
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography"
            ) as key:
                value, _ = winreg.QueryValueEx(key, "MachineGuid")
                if value:
                    return str(value)
        except OSError:
            pass
    return platform.node() or "unknown-machine"


def _cpu_name() -> str:
    name = platform.processor() or ""
    if name.strip():
        return name.strip()
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            ) as key:
                value, _ = winreg.QueryValueEx(key, "ProcessorNameString")
                if value:
                    return str(value).strip()
        except OSError:
            pass
    return f"{os.cpu_count() or 1} CPU cores"


def _total_ram_gb() -> float:
    if os.name == "nt":
        try:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MEMORYSTATUSEX()
            status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return status.ullTotalPhys / (1024 ** 3)
        except (AttributeError, OSError, ValueError):
            pass
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        pages = os.sysconf("SC_PHYS_PAGES")
        return (page_size * pages) / (1024 ** 3)
    except (AttributeError, OSError, ValueError):
        return 0.0


def _detect_gpu() -> tuple[str, float, bool]:
    nvidia = shutil.which("nvidia-smi")
    if nvidia:
        try:
            completed = subprocess.run(
                [
                    nvidia,
                    "--query-gpu=name,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
                creationflags=hidden_process_creation_flags(),
            )
            line = (completed.stdout or "").strip().splitlines()
            if completed.returncode == 0 and line:
                name, memory = [part.strip() for part in line[0].split(",", 1)]
                return name, float(memory) / 1024.0, True
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return _windows_display_adapter()


def _windows_display_adapter() -> tuple[str, float, bool]:
    if os.name != "nt":
        return "CPU only", 0.0, False
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance Win32_VideoController | "
                "Select-Object -First 1 -ExpandProperty Name)",
            ],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
            creationflags=hidden_process_creation_flags(),
        )
        name = (completed.stdout or "").strip()
        if name:
            lowered = name.lower()
            return name, 0.0, "nvidia" in lowered
    except (OSError, subprocess.SubprocessError):
        pass
    return "Unknown GPU", 0.0, False
