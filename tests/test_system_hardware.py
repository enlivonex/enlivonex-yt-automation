from types import SimpleNamespace

from app.system import hardware


def test_windows_gpu_scan_finds_nvidia_adapter_after_integrated_graphics(
    monkeypatch,
):
    monkeypatch.setattr(hardware, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        hardware.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout="Intel(R) Graphics\nNVIDIA GeForce RTX 3050\n",
            returncode=0,
        ),
    )

    assert hardware._windows_display_adapter() == (
        "NVIDIA GeForce RTX 3050",
        0.0,
        True,
    )
