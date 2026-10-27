"""Script de empacotamento para gerar o executável único dist/SentinelPC.exe via PyInstaller."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

from src.icon_gen import ensure_icon_files
from src.paths import APP_ICON_PATH, LANDMARK_106_MODEL_PATH, SFACE_MODEL_PATH, YUNET_MODEL_PATH


def build() -> int:
    root = Path(__file__).resolve().parent
    print("[1/3] Gerando ícone corporativo assets/icon.ico...")
    ensure_icon_files(APP_ICON_PATH)

    for model in (YUNET_MODEL_PATH, SFACE_MODEL_PATH, LANDMARK_106_MODEL_PATH):
        if not model.exists():
            print(f"[ERRO] Modelo ONNX não encontrado: {model}")
            return 1

    print("[2/3] Compilando executável autônomo SentinelPC.exe com PyInstaller...")
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name",
        "SentinelPC",
        "--icon",
        str(APP_ICON_PATH),
        "--collect-all",
        "customtkinter",
        "--collect-submodules",
        "pystray",
        "--add-data",
        f"{root / 'assets'};assets",
        str(root / "main.py"),
    ]

    result = subprocess.run(cmd, cwd=str(root), check=False)
    if result.returncode != 0:
        print("[ERRO] Falha durante o empacotamento PyInstaller.")
        return result.returncode

    exe_path = root / "dist" / "SentinelPC.exe"
    if exe_path.exists():
        size_mb = exe_path.stat().st_size / (1024 * 1024)
        print(f"[3/3] Sucesso! Executável pronto em: {exe_path} ({size_mb:.1f} MB)")
        return 0

    print("[ERRO] SentinelPC.exe não encontrado após o build.")
    return 1


if __name__ == "__main__":
    raise SystemExit(build())
