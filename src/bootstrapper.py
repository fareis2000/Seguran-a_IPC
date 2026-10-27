"""Bootstrapper auto-instalável em arquivo único (SentinelPC.exe):
Na primeira execução (ou atualização), extrai o motor nativo rápido (--onedir) em
%LOCALAPPDATA%\\SentinelPC\\runtime\\SentinelPC_Core.exe para eliminar 100% do atraso
de descompactação em todos os boots seguintes do Windows."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tkinter as tk
import zipfile

CREATE_NO_WINDOW = 0x08000000


def _get_bundle_dir() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parent.parent / "build"


def _get_runtime_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        base = Path(local_app_data) / "SentinelPC"
    else:
        base = Path.home() / ".sentinelpc"
    return base / "runtime"


def _stop_existing_core() -> None:
    for proc_name in ("SentinelPC_Core.exe",):
        try:
            subprocess.run(
                ["taskkill.exe", "/F", "/IM", proc_name],
                creationflags=CREATE_NO_WINDOW,
                capture_output=True,
                timeout=5,
                check=False,
            )
        except Exception:
            pass


def _extract_with_splash(payload_zip: Path, runtime_dir: Path, build_id: str) -> None:
    splash: tk.Tk | None = None
    try:
        splash = tk.Tk()
        splash.overrideredirect(True)
        splash.attributes("-topmost", True)
        splash.configure(bg="#0F172A")
        w, h = 440, 130
        sw = splash.winfo_screenwidth()
        sh = splash.winfo_screenheight()
        splash.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

        tk.Label(
            splash,
            text="🛡️ SentinelPC — Preparando Inicialização Rápida",
            font=("Segoe UI", 12, "bold"),
            fg="#38BDF8",
            bg="#0F172A",
        ).pack(pady=(26, 6))
        tk.Label(
            splash,
            text="Instalando motor nativo local para boot instantâneo no Windows...",
            font=("Segoe UI", 9),
            fg="#CBD5E1",
            bg="#0F172A",
        ).pack()
        splash.update()
    except Exception:
        splash = None

    try:
        _stop_existing_core()
        staging_dir = runtime_dir.parent / "runtime_staging"
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        staging_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(payload_zip, "r") as zf:
            zf.extractall(staging_dir)

        if runtime_dir.exists():
            shutil.rmtree(runtime_dir, ignore_errors=True)
        staging_dir.rename(runtime_dir)
        (runtime_dir / "build_id.txt").write_text(build_id, encoding="utf-8")
    finally:
        if splash is not None:
            try:
                splash.destroy()
            except Exception:
                pass


def main() -> int:
    bundle_dir = _get_bundle_dir()
    payload_zip = bundle_dir / "runtime_payload.zip"
    id_src = bundle_dir / "payload_build_id.txt"

    if not payload_zip.exists() or not id_src.exists():
        ctypes.windll.user32.MessageBoxW(
            0,
            "Pacote interno runtime_payload.zip não encontrado no instalador.",
            "SentinelPC — Erro",
            0x10,
        )
        return 1

    expected_id = id_src.read_text(encoding="utf-8").strip()
    runtime_dir = _get_runtime_dir()
    core_exe = runtime_dir / "SentinelPC_Core.exe"
    installed_id_file = runtime_dir / "build_id.txt"

    installed_id = ""
    if installed_id_file.exists():
        try:
            installed_id = installed_id_file.read_text(encoding="utf-8").strip()
        except OSError:
            installed_id = ""

    if not core_exe.exists() or installed_id != expected_id:
        try:
            _extract_with_splash(payload_zip, runtime_dir, expected_id)
        except Exception as exc:
            ctypes.windll.user32.MessageBoxW(
                0,
                f"Falha ao preparar o motor rápido do SentinelPC:\n{exc}",
                "SentinelPC — Erro",
                0x10,
            )
            return 1

    if "--install-only" in sys.argv[1:]:
        return 0

    # Inicia o motor pré-extraído nativo imediatamente (0s de descompactação nos próximos boots!)
    subprocess.Popen(
        [str(core_exe), *sys.argv[1:]],
        cwd=str(runtime_dir),
        close_fds=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
