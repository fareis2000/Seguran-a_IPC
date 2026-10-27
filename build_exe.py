"""Script de empacotamento em 2 estágios para Boot Instantâneo:
1) Compila SentinelPC_Core em modo --onedir (0s de descompactação no logon do Windows);
2) Empacota tudo dentro do arquivo único auto-instalável dist/SentinelPC.exe."""

from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

from src.icon_gen import ensure_icon_files
from src.paths import APP_ICON_PATH, LANDMARK_106_MODEL_PATH, SFACE_MODEL_PATH, YUNET_MODEL_PATH


def _zip_directory(src_dir: Path, out_zip: Path) -> str:
    hasher = hashlib.sha256()
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for file_path in sorted(src_dir.rglob("*")):
            if file_path.is_file():
                rel = file_path.relative_to(src_dir)
                zf.write(file_path, arcname=str(rel))
                hasher.update(str(rel).encode("utf-8"))
                hasher.update(file_path.read_bytes())
    return hasher.hexdigest()[:16]


def build() -> int:
    root = Path(__file__).resolve().parent
    build_dir = root / "build"
    core_dist = build_dir / "core_dist"

    print("[1/4] Gerando ícone corporativo assets/icon.ico...")
    ensure_icon_files(APP_ICON_PATH)

    for model in (YUNET_MODEL_PATH, SFACE_MODEL_PATH, LANDMARK_106_MODEL_PATH):
        if not model.exists():
            print(f"[ERRO] Modelo ONNX não encontrado: {model}")
            return 1

    if core_dist.exists():
        shutil.rmtree(core_dist, ignore_errors=True)

    print("[2/4] Compilando motor nativo rápido SentinelPC_Core (--onedir, zero atraso no boot)...")
    cmd_core = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",
        "--distpath",
        str(core_dist),
        "--workpath",
        str(build_dir / "work_core"),
        "--specpath",
        str(build_dir),
        "--name",
        "SentinelPC_Core",
        "--icon",
        str(APP_ICON_PATH),
        "--hidden-import",
        "cv2",
        "--hidden-import",
        "numpy",
        "--hidden-import",
        "PIL",
        "--hidden-import",
        "pystray",
        "--hidden-import",
        "src.vision_engine",
        "--hidden-import",
        "src.ui_admin_panel",
        "--hidden-import",
        "src.icon_gen",
        "--collect-all",
        "customtkinter",
        "--collect-submodules",
        "pystray",
        "--add-data",
        f"{root / 'assets'};assets",
        str(root / "main.py"),
    ]
    res_core = subprocess.run(cmd_core, cwd=str(root), check=False)
    if res_core.returncode != 0:
        print("[ERRO] Falha ao compilar SentinelPC_Core.")
        return res_core.returncode

    core_folder = core_dist / "SentinelPC_Core"
    payload_zip = build_dir / "runtime_payload.zip"
    id_file = build_dir / "payload_build_id.txt"

    print("[3/4] Compactando motor nativo para distribuição em arquivo único...")
    build_id = _zip_directory(core_folder, payload_zip)
    id_file.write_text(build_id, encoding="utf-8")
    print(f"      Build ID gerado: {build_id}")

    print("[4/4] Gerando executável único auto-instalável dist/SentinelPC.exe...")
    cmd_boot = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--distpath",
        str(root / "dist"),
        "--workpath",
        str(build_dir / "work_boot"),
        "--specpath",
        str(build_dir),
        "--name",
        "SentinelPC",
        "--icon",
        str(APP_ICON_PATH),
        "--exclude-module",
        "cv2",
        "--exclude-module",
        "numpy",
        "--exclude-module",
        "PIL",
        "--exclude-module",
        "customtkinter",
        "--exclude-module",
        "pystray",
        "--add-data",
        f"{payload_zip};.",
        "--add-data",
        f"{id_file};.",
        str(root / "src" / "bootstrapper.py"),
    ]
    res_boot = subprocess.run(cmd_boot, cwd=str(root), check=False)
    if res_boot.returncode != 0:
        print("[ERRO] Falha ao compilar dist/SentinelPC.exe.")
        return res_boot.returncode

    exe_path = root / "dist" / "SentinelPC.exe"
    if exe_path.exists():
        size_mb = exe_path.stat().st_size / (1024 * 1024)
        print(f"Sucesso! Executável único pronto em: {exe_path} ({size_mb:.1f} MB)")
        return 0

    print("[ERRO] dist/SentinelPC.exe não encontrado após o build.")
    return 1


if __name__ == "__main__":
    raise SystemExit(build())
