"""Gerenciamento centralizado de caminhos de recursos (dev + PyInstaller) e dados locais."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "SentinelPC"
APP_TITLE = "SentinelPC — Controle Facial Corporativo"
APP_VERSION = "1.0.0"
MUTEX_NAME = "Local\\SentinelPC_CorporateFaceAuth_SingleInstance"


def get_base_dir() -> Path:
    """Retorna o diretório base dos recursos empacotados (suporta PyInstaller _MEIPASS)."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parent.parent


def get_resource_path(relative_path: str) -> Path:
    """Resolve o caminho absoluto de um arquivo estático em assets/."""
    return get_base_dir() / relative_path


def get_executable_command(startup_mode: bool = True) -> str:
    """Retorna a linha de comando exata para registrar na inicialização do Windows."""
    flag = " --startup" if startup_mode else ""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"{flag}'
    main_script = Path(__file__).resolve().parent.parent / "main.py"
    # Usa pythonw.exe quando disponível para não abrir janela de console no logon
    py_exe = Path(sys.executable)
    pythonw = py_exe.with_name("pythonw.exe")
    runner = pythonw if pythonw.exists() else py_exe
    return f'"{runner}" "{main_script}"{flag}'


def get_data_dir() -> Path:
    """Retorna (e cria se necessário) o diretório seguro de dados locais no Windows."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        base = Path(local_app_data) / APP_NAME
    else:
        base = Path.home() / f".{APP_NAME.lower()}"
    base.mkdir(parents=True, exist_ok=True)
    return base


def get_intruders_dir() -> Path:
    """Diretório onde as fotos de tentativas não autorizadas são salvas."""
    path = get_data_dir() / "intrusos"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_logs_dir() -> Path:
    """Diretório para logs técnicos de execução."""
    path = get_data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_db_path() -> Path:
    """Caminho do banco SQLite local."""
    return get_data_dir() / "sentinel_pc.db"


YUNET_MODEL_PATH = get_resource_path("assets/models/face_detection_yunet_2023mar.onnx")
SFACE_MODEL_PATH = get_resource_path("assets/models/face_recognition_sface_2021dec.onnx")
LANDMARK_106_MODEL_PATH = get_resource_path("assets/models/2d106det.onnx")
APP_ICON_PATH = get_resource_path("assets/icon.ico")
