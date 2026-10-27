"""Tela de Bloqueio Kiosk Fullscreen (Multi-Monitor) com Reconhecimento Facial,
Prova de Vida por Piscada, Registro Fotográfico de Intrusos e Contingência da TI."""

from __future__ import annotations

from datetime import datetime
import socket
import time
from typing import Callable

import customtkinter as ctk
import cv2
import numpy as np
from PIL import Image

from src.storage import EnrolledUser, StorageManager
from src.vision_engine import BiometricVisionEngine, draw_hud_overlay, open_fast_camera
from src.win_security import WinSecurityController, get_all_monitors


class LockScreenOverlay:
    """Gerencia o bloqueio em tela cheia no monitor principal e blackout nos secundários."""

    def __init__(
        self,
        master: ctk.CTk,
        storage: StorageManager,
        vision: BiometricVisionEngine,
        win_security: WinSecurityController,
        on_unlocked: Callable[[str], None] | None = None,
        on_open_admin: Callable[[], None] | None = None,
        reason: str = "Autenticação obrigatória",
    ) -> None:
        self.master = master
        self.storage = storage
        self.vision = vision
        self.win_security = win_security
        self.on_unlocked = on_unlocked
        self.on_open_admin = on_open_admin
        self.reason = reason

        self.is_active = True
        self._unlocking = False
        self._secondary_windows: list[ctk.CTkToplevel] = []
        self._cap: cv2.VideoCapture | None = None
        self._last_raw_frame: np.ndarray | None = None
        self._last_cam_retry: float = 0.0
        self._last_intruder_shot: float = 0.0
        self._last_spoof_shot: float = 0.0
        self._alert_until: float = 0.0
        self._alert_text: str = ""

        self._enrolled_users: list[EnrolledUser] = self.storage.list_users(only_active=True)
        self._threshold: float = self.storage.get_float_setting("similarity_threshold", 0.38)
        self._require_liveness: bool = self.storage.get_bool_setting("require_liveness", True)
        self._camera_index: int = self.storage.get_int_setting("camera_index", 0)
        self._cooldown_sec: float = float(
            self.storage.get_int_setting("intruder_cooldown_sec", 8)
        )

        self.vision.reset_tracker()
        self.win_security.set_kiosk_lock(True)

        monitors = get_all_monitors()
        primary = monitors[0]
        secondaries = monitors[1:]

        # Define dimensões do vídeo adaptadas à altura do monitor (ex: 768p vs 1080p)
        if primary.height <= 820:
            self._cam_w, self._cam_h = 460, 345
        else:
            self._cam_w, self._cam_h = 560, 420

        blank_pil = Image.new("RGB", (self._cam_w, self._cam_h), (2, 6, 23))
        self._blank_ctk_img = ctk.CTkImage(
            light_image=blank_pil, dark_image=blank_pil, size=(self._cam_w, self._cam_h)
        )

        # Cria janela principal Kiosk
        self.win = ctk.CTkToplevel(self.master)
        self.win.title("SentinelPC — Estação Bloqueada")
        self.win.configure(fg_color="#070B14")
        self.win.overrideredirect(True)
        self.win.geometry(f"{primary.width}x{primary.height}+{primary.x}+{primary.y}")
        self.win.attributes("-topmost", True)
        self.win.protocol("WM_DELETE_WINDOW", lambda: None)

        # Cria janelas blackout para todos os monitores secundários
        for mon in secondaries:
            sec = ctk.CTkToplevel(self.master)
            sec.configure(fg_color="#05080F")
            sec.overrideredirect(True)
            sec.geometry(f"{mon.width}x{mon.height}+{mon.x}+{mon.y}")
            sec.attributes("-topmost", True)
            sec.protocol("WM_DELETE_WINDOW", lambda: None)
            lbl = ctk.CTkLabel(
                sec,
                text="🔒 ESTAÇÃO CORPORATIVA BLOQUEADA\n\nRealize o reconhecimento facial no monitor principal\npara liberar o uso deste computador.",
                font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"),
                text_color="#64748B",
                justify="center",
            )
            lbl.place(relx=0.5, rely=0.5, anchor="center")
            self._secondary_windows.append(sec)

        self._build_ui(primary.width, primary.height)
        self._open_camera_if_needed()

        self.win.lift()
        self.win.focus_force()

        self._tick_clock()
        self._tick_watchdog()
        self._tick_camera()

    def _build_ui(self, screen_w: int, screen_h: int) -> None:
        compact = screen_h <= 820

        # Barra Superior
        header = ctk.CTkFrame(self.win, fg_color="#0F172A", corner_radius=0, height=58)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)

        title_lbl = ctk.CTkLabel(
            header,
            text="🛡️ SENTINEL PC  •  AUTENTICAÇÃO FACIAL CORPORATIVA",
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
            text_color="#38BDF8",
        )
        title_lbl.pack(side="left", padx=24)

        self._clock_lbl = ctk.CTkLabel(
            header,
            text="",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            text_color="#E2E8F0",
        )
        self._clock_lbl.pack(side="right", padx=24)

        host_lbl = ctk.CTkLabel(
            header,
            text=f"Estação: {socket.gethostname()}  |  {self.reason}",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#94A3B8",
        )
        host_lbl.pack(side="right", padx=16)

        # Rodapé
        footer = ctk.CTkFrame(self.win, fg_color="#0F172A", corner_radius=0, height=54)
        footer.pack(fill="x", side="bottom")
        footer.pack_propagate(False)

        policy_txt = (
            "📷 Monitoramento ativo: tentativas de acesso por terceiros são fotografadas com data e hora."
        )
        ctk.CTkLabel(
            footer,
            text=policy_txt,
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#94A3B8",
        ).pack(side="left", padx=24)

        self._contingency_btn = ctk.CTkButton(
            footer,
            text="🔑 Contingência / Senha TI",
            width=195,
            height=34,
            fg_color="#1E293B",
            hover_color="#334155",
            border_width=1,
            border_color="#475569",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            command=self._toggle_contingency_panel,
        )
        self._contingency_btn.pack(side="right", padx=24, pady=10)

        # Container Central
        center = ctk.CTkFrame(
            self.win,
            fg_color="#0F172A",
            corner_radius=18,
            border_width=2,
            border_color="#1E293B",
        )
        center.place(relx=0.5, rely=0.5, anchor="center")
        self._center_card = center

        pad_y = 12 if compact else 20

        self._headline_lbl = ctk.CTkLabel(
            center,
            text="Identifique-se para Liberar o Computador",
            font=ctk.CTkFont(family="Segoe UI", size=20 if compact else 24, weight="bold"),
            text_color="#F8FAFC",
        )
        self._headline_lbl.pack(padx=32, pady=(pad_y, 2))

        sub_hint = (
            "Olhe para a câmera e pisque os olhos naturalmente para validar a prova de vida"
            if self._require_liveness
            else "Posicione seu rosto de frente para a câmera"
        )
        self._sub_lbl = ctk.CTkLabel(
            center,
            text=sub_hint,
            font=ctk.CTkFont(family="Segoe UI", size=13),
            text_color="#94A3B8",
        )
        self._sub_lbl.pack(padx=32, pady=(0, 8))

        # Moldura da Câmera
        self._cam_border = ctk.CTkFrame(
            center,
            fg_color="#020617",
            corner_radius=12,
            border_width=2,
            border_color="#0EA5E9",
            width=self._cam_w + 8,
            height=self._cam_h + 8,
        )
        self._cam_border.pack(padx=28, pady=4)
        self._cam_border.pack_propagate(False)

        self._video_lbl = ctk.CTkLabel(
            self._cam_border,
            text="Inicializando câmera segura...",
            text_color="#94A3B8",
            font=ctk.CTkFont(family="Segoe UI", size=14),
        )
        self._video_lbl.place(relx=0.5, rely=0.5, anchor="center")

        # Barra de Status Biométrico / Prova de Vida
        self._status_lbl = ctk.CTkLabel(
            center,
            text="Aguardando leitura facial...",
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
            text_color="#38BDF8",
        )
        self._status_lbl.pack(padx=28, pady=(8, 4))

        self._progress = ctk.CTkProgressBar(
            center, width=self._cam_w, height=8, progress_color="#38BDF8"
        )
        self._progress.set(0.15)
        self._progress.pack(padx=28, pady=(0, pad_y))

        # Painel Oculto de Senha de Contingência da TI (inline para nunca perder foco no Kiosk)
        self._contingency_frame = ctk.CTkFrame(
            center, fg_color="#1E293B", corner_radius=10
        )
        self._contingency_visible = False

        row = ctk.CTkFrame(self._contingency_frame, fg_color="transparent")
        row.pack(padx=14, pady=10, fill="x")

        self._pwd_entry = ctk.CTkEntry(
            row,
            placeholder_text="Senha Mestre da TI...",
            show="•",
            width=210,
            height=34,
            font=ctk.CTkFont(family="Segoe UI", size=13),
        )
        self._pwd_entry.pack(side="left", padx=(0, 8))
        self._pwd_entry.bind("<Return>", lambda _e: self._submit_contingency_unlock(open_admin=False))

        ctk.CTkButton(
            row,
            text="Liberar Acesso",
            width=118,
            height=34,
            fg_color="#0284C7",
            hover_color="#0369A1",
            command=lambda: self._submit_contingency_unlock(open_admin=False),
        ).pack(side="left", padx=(0, 6))

        ctk.CTkButton(
            row,
            text="Painel TI",
            width=92,
            height=34,
            fg_color="#334155",
            hover_color="#475569",
            command=lambda: self._submit_contingency_unlock(open_admin=True),
        ).pack(side="left")

        self._pwd_feedback_lbl = ctk.CTkLabel(
            self._contingency_frame,
            text="",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#F87171",
        )
        self._pwd_feedback_lbl.pack(padx=12, pady=(0, 6))

    def _toggle_contingency_panel(self) -> None:
        if not self.is_active:
            return
        if self._contingency_visible:
            self._contingency_frame.pack_forget()
            self._contingency_visible = False
        else:
            self._pwd_feedback_lbl.configure(text="")
            self._pwd_entry.delete(0, "end")
            self._contingency_frame.pack(padx=28, pady=(0, 12), fill="x")
            self._contingency_visible = True
            self.win.focus_force()
            self._pwd_entry.focus_set()

    def _submit_contingency_unlock(self, open_admin: bool = False) -> None:
        if not self.is_active or self._unlocking:
            return
        typed = self._pwd_entry.get()
        if self.storage.verify_admin_password(typed):
            self.storage.log_event(
                event_type="CONTINGENCIA_TI",
                person_label="Administrador TI",
                details="Desbloqueio realizado via Senha Mestre da TI",
            )
            self._complete_unlock("Contingência TI", trigger_admin=open_admin)
        else:
            self._pwd_entry.delete(0, "end")
            self._pwd_feedback_lbl.configure(
                text="❌ Senha da TI inválida! Tentativa registrada com foto."
            )
            self.storage.log_event(
                event_type="SENHA_TI_INVALIDA",
                person_label="Desconhecido",
                frame_bgr=self._last_raw_frame,
                details="Tentativa incorreta de Senha Mestre da TI na tela de bloqueio",
            )

    def _open_camera_if_needed(self) -> None:
        if self._cap is not None and self._cap.isOpened():
            return
        now = time.monotonic()
        if now - self._last_cam_retry < 2.5:
            return
        self._last_cam_retry = now
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
        self._cap = open_fast_camera(self._camera_index, 640, 480)

    def _tick_clock(self) -> None:
        if not self.is_active:
            return
        now_str = datetime.now().strftime("%d/%m/%Y  •  %H:%M:%S")
        self._clock_lbl.configure(text=now_str)
        self.win.after(1000, self._tick_clock)

    def _tick_watchdog(self) -> None:
        """Mantém a janela Kiosk sempre em primeiro plano sobre todos os monitores."""
        if not self.is_active:
            return
        try:
            for sec in self._secondary_windows:
                sec.attributes("-topmost", True)
                sec.lift()
            self.win.attributes("-topmost", True)
            self.win.lift()
            if not self._contingency_visible:
                self.win.focus_force()
        except Exception:
            pass
        self.win.after(250, self._tick_watchdog)

    def _tick_camera(self) -> None:
        if not self.is_active or self._unlocking:
            return

        self._open_camera_if_needed()
        if self._cap is None or not self._cap.isOpened():
            self._video_lbl.configure(
                image=self._blank_ctk_img,
                compound="center",
                text="⚠️ Webcam não disponível no momento.\nConecte a câmera ou use 'Contingência / Senha TI' abaixo.",
            )
            self._status_lbl.configure(
                text="Aguardando conexão da câmera...", text_color="#FBBF24"
            )
            self.win.after(500, self._tick_camera)
            return

        ok, frame_bgr = self._cap.read()
        if not ok or frame_bgr is None or frame_bgr.size == 0:
            self._cap.release()
            self._cap = None
            self.win.after(300, self._tick_camera)
            return

        self._last_raw_frame = frame_bgr.copy()

        res = self.vision.analyze_frame(
            frame_bgr=frame_bgr,
            enrolled_users=self._enrolled_users,
            threshold=self._threshold,
            require_liveness=self._require_liveness,
        )

        now = time.monotonic()

        # 1. Verifica sucesso de autenticação!
        if res.authenticated and res.matched_user is not None:
            self._cam_border.configure(border_color="#22C55E")
            self._status_lbl.configure(text=res.status_message, text_color="#4ADE80")
            self._progress.configure(progress_color="#22C55E")
            self._progress.set(1.0)
            self._render_frame(frame_bgr, res)
            self.storage.log_event(
                event_type="ACESSO_LIBERADO",
                person_label=res.matched_user.name,
                similarity_score=res.best_score,
                details="Reconhecimento facial + prova de vida aprovados",
            )
            self._unlocking = True
            self.win.after(
                320, lambda u=res.matched_user.name: self._complete_unlock(u, trigger_admin=False)
            )
            return

        # 2. Auditoria automática se rosto NÃO autorizado permanecer >= 1.4s olhando para a tela
        if (
            res.liveness_stage == "NAO_AUTORIZADO"
            and self.vision.tracker.unauthorized_since is not None
        ):
            elapsed_unauth = now - self.vision.tracker.unauthorized_since
            if (
                elapsed_unauth >= 1.4
                and (now - self._last_intruder_shot) >= self._cooldown_sec
            ):
                self._last_intruder_shot = now
                self._alert_until = now + 3.2
                self._alert_text = (
                    "🚨 ACESSO NEGADO — Foto e horário registrados na auditoria!"
                )
                self.storage.log_event(
                    event_type="INTRUSO_DETECTADO",
                    person_label="Não Autorizado",
                    similarity_score=res.best_score,
                    frame_bgr=frame_bgr,
                    details="Pessoa não cadastrada tentou utilizar o computador",
                )

        # 3. Auditoria de possível fraude por foto/celular (rosto reconhecido sem piscar por >= 9s)
        if (
            res.matched_user is not None
            and self._require_liveness
            and (now - self.vision.tracker.track_started_at) >= 9.0
            and (now - self._last_spoof_shot) >= 12.0
        ):
            self._last_spoof_shot = now
            self._alert_until = now + 3.2
            self._alert_text = (
                "⚠️ Prova de vida não confirmada — Pisque os olhos (foto registrada)"
            )
            self.storage.log_event(
                event_type="FALHA_PROVA_VIDA",
                person_label=res.matched_user.name,
                similarity_score=res.best_score,
                frame_bgr=frame_bgr,
                details="Rosto semelhante sem piscar por 9s (possível foto em celular)",
            )
            self.vision.reset_tracker()

        # 4. Atualiza cores e barra de progresso conforme estágio
        if now < self._alert_until:
            self._cam_border.configure(border_color="#EF4444")
            self._status_lbl.configure(text=self._alert_text, text_color="#F87171")
            self._progress.configure(progress_color="#EF4444")
            self._progress.set(1.0)
        elif res.matched_user is not None:
            self._cam_border.configure(border_color="#F59E0B")
            self._status_lbl.configure(text=res.status_message, text_color="#FCD34D")
            self._progress.configure(progress_color="#F59E0B")
            if res.liveness_stage == "PISCANDO" or res.liveness_stage == "REABRINDO_OLHOS":
                self._progress.set(0.80)
            else:
                self._progress.set(0.55)
        elif res.liveness_stage == "NAO_AUTORIZADO":
            self._cam_border.configure(border_color="#EF4444")
            self._status_lbl.configure(text=res.status_message, text_color="#F87171")
            self._progress.configure(progress_color="#EF4444")
            self._progress.set(0.95)
        else:
            self._cam_border.configure(border_color="#0EA5E9")
            self._status_lbl.configure(text=res.status_message, text_color="#38BDF8")
            self._progress.configure(progress_color="#0EA5E9")
            self._progress.set(0.18)

        self._render_frame(frame_bgr, res)
        self.win.after(33, self._tick_camera)

    def _render_frame(self, frame_bgr: np.ndarray, res) -> None:
        hud_bgr = draw_hud_overlay(frame_bgr, res, mirror=True)
        rgb = cv2.cvtColor(hud_bgr, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(rgb)
        ctk_img = ctk.CTkImage(
            light_image=pil_img, dark_image=pil_img, size=(self._cam_w, self._cam_h)
        )
        self._video_lbl.configure(image=ctk_img, text="")
        self._video_lbl._current_image = ctk_img  # Evita coleta de lixo da imagem Tk

    def _complete_unlock(self, user_label: str, trigger_admin: bool = False) -> None:
        self.close()
        if trigger_admin and self.on_open_admin is not None:
            self.on_open_admin()
        elif self.on_unlocked is not None:
            self.on_unlocked(user_label)

    def close(self) -> None:
        if not self.is_active:
            return
        self.is_active = False
        self.win_security.set_kiosk_lock(False)
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None

        for sec in self._secondary_windows:
            try:
                sec.destroy()
            except Exception:
                pass
        self._secondary_windows.clear()

        try:
            self.win.destroy()
        except Exception:
            pass
