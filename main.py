"""Ponto de entrada do SentinelPC — Autenticação Facial Corporativa para Windows."""

from __future__ import annotations

import argparse
from datetime import datetime
import logging
import sys
import traceback

from src.paths import get_logs_dir
from src.win_security import set_taskbar_visible


def configure_logging() -> None:
    log_file = get_logs_dir() / "sentinel_pc.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(str(log_file), encoding="utf-8")],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="SentinelPC — Autenticação Facial Corporativa")
    parser.add_argument(
        "--startup",
        action="store_true",
        help="Indica que o aplicativo foi iniciado automaticamente no logon do Windows.",
    )
    parser.add_argument(
        "--admin",
        action="store_true",
        help="Solicita abertura direta do Painel Administrativo da TI.",
    )
    args = parser.parse_args()

    configure_logging()
    logging.info("Iniciando SentinelPC (startup=%s, admin=%s)", args.startup, args.admin)

    try:
        from src.app_controller import SentinelAppController

        app = SentinelAppController(
            startup_mode=bool(args.startup),
            open_admin_on_start=bool(args.admin),
        )
        return app.run()
    except Exception as exc:
        set_taskbar_visible(True)
        err_trace = traceback.format_exc()
        logging.error("Erro fatal no SentinelPC:\n%s", err_trace)
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                0,
                f"Erro ao iniciar o SentinelPC ({datetime.now().strftime('%H:%M:%S')}):\n{exc}",
                "SentinelPC — Erro",
                0x10,
            )
        except Exception:
            pass
        return 1
    finally:
        set_taskbar_visible(True)


if __name__ == "__main__":
    sys.exit(main())
