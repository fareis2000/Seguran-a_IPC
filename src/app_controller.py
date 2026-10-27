"""Coordenador central do SentinelPC com Instant-Lock no Boot:
trava teclado e monitores imediatamente antes de carregar OpenCV/ONNX/Bandeja."""

from __future__ import annotations

import ctypes
import queue
import threading
from typing import Any

import customtkinter as ctk

from src.paths import APP_ICON_PATH, APP_NAME, APP_TITLE
from src.storage import StorageManager
from src.ui_lock_screen import LockScreenOverlay
from src.win_security import (
    SingleInstanceGuard,
    WinSecurityController,
    boost_process_priority,
    ensure_fast_startup_synced,
    set_taskbar_visible,
)


class SentinelAppController:
    """Orquestra a proteção contínua na sessão do Windows com inicialização instantânea."""

    def __init__(self, startup_mode: bool = False, open_admin_on_start: bool = False) -> None:
        boost_process_priority()
        self.startup_mode = startup_mode
        self.open_admin_on_start = open_admin_on_start
        self._guard = SingleInstanceGuard()
        self._event_queue: queue.Queue[str] = queue.Queue()

        self.vision: Any = None
        self._lock_overlay: LockScreenOverlay | None = None
        self._admin_window: Any = None
        self._auth_dialog: Any = None
        self._tray_icon: Any = None
        self._shutting_down = False

        self.storage = StorageManager()
        self.win_security = WinSecurityController(on_system_event=self._enqueue_event)

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.root = ctk.CTk()
        self.root.withdraw()
        self.root.title(APP_TITLE)

    def _enqueue_event(self, event_name: str) -> None:
        self._event_queue.put(event_name)

    def _ensure_vision_engine(self) -> Any:
        if self.vision is None:
            from src.vision_engine import BiometricVisionEngine

            self.vision = BiometricVisionEngine()
        return self.vision

    def _on_vision_loaded_async(self, engine: Any) -> None:
        self.vision = engine

    def run(self) -> int:
        if self._guard.already_running:
            if not self.startup_mode:
                ctypes.windll.user32.MessageBoxW(
                    0,
                    "O SentinelPC já está ativo em segundo plano!\n\n"
                    "Procure o ícone de escudo azul na Bandeja do Sistema "
                    "(próximo ao relógio do Windows) para bloquear ou abrir o Painel da TI.",
                    APP_TITLE,
                    0x40,
                )
            return 0

        self.win_security.start()

        admin_ok = self.storage.is_admin_configured()
        has_users = self.storage.has_enrolled_users()
        lock_on_boot = self.storage.get_bool_setting("lock_on_startup", True)

        # Caminho ultrarrápido: bloqueia monitores + teclado IMEDIATAMENTE antes da bandeja/OpenCV!
        if admin_ok and has_users and not self.open_admin_on_start and lock_on_boot:
            reason = (
                "Inicialização do Windows"
                if self.startup_mode
                else "Início de Sessão Protegida"
            )
            self.activate_lock_screen(reason=reason)
        else:
            self.root.after(40, self._deferred_non_lock_dispatch)

        # Inicializa ícone de bandeja e sincronização da Tarefa Agendada sem atrasar o bloqueio
        self.root.after(200, self._post_lock_background_init)
        self.root.after(150, self._poll_queue)

        try:
            self.root.mainloop()
        finally:
            self._cleanup()
        return 0

    def _post_lock_background_init(self) -> None:
        if self._shutting_down:
            return
        if not APP_ICON_PATH.exists():
            try:
                from src.icon_gen import ensure_icon_files

                ensure_icon_files(APP_ICON_PATH)
            except Exception:
                pass

        self._start_tray_icon()

        if self.storage.is_admin_configured() and self.storage.get_bool_setting(
            "lock_on_startup", True
        ):
            ensure_fast_startup_synced(self.storage)

    def _deferred_non_lock_dispatch(self) -> None:
        if not self.storage.is_admin_configured():
            from src.ui_admin_panel import FirstSetupWizard

            FirstSetupWizard(
                master=self.root,
                storage=self.storage,
                on_completed=self._open_admin_panel_direct,
            )
            return

        if not self.storage.has_enrolled_users():
            if not self.startup_mode or self.open_admin_on_start:
                self._prompt_admin_panel()
            return

        if self.open_admin_on_start:
            self._prompt_admin_panel()

    def _poll_queue(self) -> None:
        if self._shutting_down:
            return
        try:
            while True:
                ev = self._event_queue.get_nowait()
                self._handle_event(ev)
        except queue.Empty:
            pass
        self.root.after(150, self._poll_queue)

    def _handle_event(self, ev: str) -> None:
        if ev in ("SESSION_UNLOCK", "POWER_RESUME"):
            if self.storage.get_bool_setting(
                "lock_on_unlock", True
            ) and self.storage.has_enrolled_users():
                label = (
                    "Desbloqueio de Tela (Win+L)"
                    if ev == "SESSION_UNLOCK"
                    else "Retorno de Suspensão"
                )
                self.activate_lock_screen(reason=label)
        elif ev == "TRAY_LOCK_NOW":
            if self.storage.has_enrolled_users():
                self.activate_lock_screen(reason="Bloqueio Manual")
            else:
                self._prompt_admin_panel()
        elif ev == "TRAY_OPEN_ADMIN":
            if self._lock_overlay is not None and self._lock_overlay.is_active:
                return
            self._prompt_admin_panel()
        elif ev == "TRAY_QUIT":
            if self._lock_overlay is not None and self._lock_overlay.is_active:
                return
            self._prompt_quit()

    def activate_lock_screen(self, reason: str = "Autenticação obrigatória") -> None:
        if self._lock_overlay is not None and self._lock_overlay.is_active:
            return

        if self._admin_window is not None and self._admin_window.is_open:
            self._admin_window.close()
            self._admin_window = None
        if self._auth_dialog is not None and self._auth_dialog.winfo_exists():
            self._auth_dialog.destroy()
            self._auth_dialog = None

        self._lock_overlay = LockScreenOverlay(
            master=self.root,
            storage=self.storage,
            vision=self.vision,
            win_security=self.win_security,
            on_unlocked=self._on_lock_released,
            on_open_admin=self._open_admin_panel_direct,
            on_vision_ready=self._on_vision_loaded_async,
            reason=reason,
        )

    def _on_lock_released(self, _user_label: str) -> None:
        self._lock_overlay = None

    def _prompt_admin_panel(self) -> None:
        from src.ui_admin_panel import AdminPasswordDialog, FirstSetupWizard

        if not self.storage.is_admin_configured():
            FirstSetupWizard(
                master=self.root,
                storage=self.storage,
                on_completed=self._open_admin_panel_direct,
            )
            return

        if self._admin_window is not None and self._admin_window.is_open:
            self._admin_window.win.lift()
            self._admin_window.win.focus_force()
            return

        if self._auth_dialog is not None and self._auth_dialog.winfo_exists():
            self._auth_dialog.lift()
            self._auth_dialog.focus_force()
            return

        self._auth_dialog = AdminPasswordDialog(
            master=self.root,
            storage=self.storage,
            title_text="Acesso ao Painel da TI",
            subtitle_text="Informe a Senha Mestre da TI para gerenciar rostos autorizados e fotos de intrusos.",
            on_authorized=self._open_admin_panel_direct,
        )

    def _open_admin_panel_direct(self) -> None:
        from src.ui_admin_panel import AdminPanelWindow

        self._lock_overlay = None
        if self._admin_window is not None and self._admin_window.is_open:
            self._admin_window.win.lift()
            self._admin_window.win.focus_force()
            return

        vision = self._ensure_vision_engine()
        self._admin_window = AdminPanelWindow(
            master=self.root,
            storage=self.storage,
            vision=vision,
            on_test_lock=lambda: self.activate_lock_screen("Teste de Bloqueio (TI)"),
            on_closed=self._on_admin_closed,
        )

    def _on_admin_closed(self) -> None:
        self._admin_window = None

    def _prompt_quit(self) -> None:
        from src.ui_admin_panel import AdminPasswordDialog

        if not self.storage.is_admin_configured():
            self.shutdown()
            return

        if self._auth_dialog is not None and self._auth_dialog.winfo_exists():
            self._auth_dialog.lift()
            self._auth_dialog.focus_force()
            return

        self._auth_dialog = AdminPasswordDialog(
            master=self.root,
            storage=self.storage,
            title_text="Encerrar Proteção SentinelPC",
            subtitle_text="Digite a Senha Mestre da TI para desativar o monitoramento nesta sessão.",
            on_authorized=self.shutdown,
        )

    def _start_tray_icon(self) -> None:
        if self._tray_icon is not None:
            return
        import pystray
        from src.icon_gen import create_shield_icon

        tray_img = create_shield_icon(64)
        menu = pystray.Menu(
            pystray.MenuItem(
                "🔒 Bloquear Estação Agora",
                lambda _icon, _item: self._enqueue_event("TRAY_LOCK_NOW"),
                default=True,
            ),
            pystray.MenuItem(
                "🛡️ Painel Administrativo da TI",
                lambda _icon, _item: self._enqueue_event("TRAY_OPEN_ADMIN"),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Encerrar Proteção (Senha TI)",
                lambda _icon, _item: self._enqueue_event("TRAY_QUIT"),
            ),
        )
        self._tray_icon = pystray.Icon(APP_NAME, tray_img, APP_TITLE, menu)
        threading.Thread(
            target=self._tray_icon.run, name="SentinelTrayThread", daemon=True
        ).start()

    def shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        self._cleanup()
        try:
            self.root.quit()
            self.root.destroy()
        except Exception:
            pass

    def _cleanup(self) -> None:
        try:
            if self._lock_overlay is not None and self._lock_overlay.is_active:
                self._lock_overlay.close()
        except Exception:
            pass
        try:
            if self._admin_window is not None and self._admin_window.is_open:
                self._admin_window.close()
        except Exception:
            pass
        try:
            self.win_security.stop()
        except Exception:
            pass
        try:
            set_taskbar_visible(True)
        except Exception:
            pass
        try:
            if self._tray_icon is not None:
                self._tray_icon.stop()
                self._tray_icon = None
        except Exception:
            pass
        try:
            self._guard.release()
        except Exception:
            pass
