"""Painel Administrativo da TI: Configuração inicial, cadastro facial multi-amostra,
simulador de prova de vida, galeria de fotos de intrusos e políticas do Windows."""

from __future__ import annotations

import os
from pathlib import Path
import time
from typing import Callable

import customtkinter as ctk
import cv2
import numpy as np
from PIL import Image

from src.paths import APP_ICON_PATH, APP_VERSION, get_data_dir, get_intruders_dir
from src.storage import AuditRecord, EnrolledUser, StorageManager
from src.vision_engine import BiometricVisionEngine, draw_hud_overlay, open_fast_camera
from src.win_security import is_windows_startup_enabled, set_windows_startup_enabled


class AdminPasswordDialog(ctk.CTkToplevel):
    """Modal compacto para solicitar a Senha Mestre da TI antes de ações restritas."""

    def __init__(
        self,
        master: ctk.CTk,
        storage: StorageManager,
        title_text: str,
        subtitle_text: str,
        on_authorized: Callable[[], None],
    ) -> None:
        super().__init__(master)
        self.storage = storage
        self.on_authorized = on_authorized

        self.title(title_text)
        self.geometry("420x235")
        self.resizable(False, False)
        self.configure(fg_color="#0F172A")
        self.attributes("-topmost", True)
        if APP_ICON_PATH.exists():
            try:
                self.after(200, lambda: self.iconbitmap(str(APP_ICON_PATH)))
            except Exception:
                pass

        # Centraliza na tela
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry(f"420x235+{(sw - 420) // 2}+{(sh - 235) // 2}")

        ctk.CTkLabel(
            self,
            text=f"🔐 {title_text}",
            font=ctk.CTkFont(family="Segoe UI", size=17, weight="bold"),
            text_color="#F8FAFC",
        ).pack(padx=24, pady=(20, 4))

        ctk.CTkLabel(
            self,
            text=subtitle_text,
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#94A3B8",
            wraplength=370,
        ).pack(padx=24, pady=(0, 14))

        self._pwd_entry = ctk.CTkEntry(
            self,
            placeholder_text="Digite a Senha Mestre da TI...",
            show="•",
            width=340,
            height=36,
            font=ctk.CTkFont(family="Segoe UI", size=13),
        )
        self._pwd_entry.pack(padx=24, pady=(0, 6))
        self._pwd_entry.bind("<Return>", lambda _e: self._verify())

        self._err_lbl = ctk.CTkLabel(
            self,
            text="",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#F87171",
        )
        self._err_lbl.pack(padx=24, pady=(0, 8))

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(padx=24, pady=(0, 16))

        ctk.CTkButton(
            btn_row,
            text="Cancelar",
            width=120,
            height=34,
            fg_color="#334155",
            hover_color="#475569",
            command=self.destroy,
        ).pack(side="left", padx=6)

        ctk.CTkButton(
            btn_row,
            text="Confirmar",
            width=150,
            height=34,
            fg_color="#0284C7",
            hover_color="#0369A1",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            command=self._verify,
        ).pack(side="left", padx=6)

        self.lift()
        self.focus_force()
        self._pwd_entry.focus_set()

    def _verify(self) -> None:
        pwd = self._pwd_entry.get()
        if self.storage.verify_admin_password(pwd):
            cb = self.on_authorized
            self.destroy()
            cb()
        else:
            self._pwd_entry.delete(0, "end")
            self._err_lbl.configure(text="❌ Senha incorreta! Acesso restrito à TI.")
            self.storage.log_event(
                event_type="SENHA_TI_INVALIDA",
                person_label="Desconhecido",
                details="Tentativa inválida de abertura/encerramento via Bandeja do Sistema",
            )


class FirstSetupWizard(ctk.CTkToplevel):
    """Assistente exibido na primeira execução para criar a Senha Mestre da TI."""

    def __init__(
        self,
        master: ctk.CTk,
        storage: StorageManager,
        on_completed: Callable[[], None],
    ) -> None:
        super().__init__(master)
        self.storage = storage
        self.on_completed = on_completed

        self.title("SentinelPC — Configuração Inicial da TI")
        self.geometry("480x360")
        self.resizable(False, False)
        self.configure(fg_color="#0F172A")
        self.attributes("-topmost", True)
        if APP_ICON_PATH.exists():
            try:
                self.after(200, lambda: self.iconbitmap(str(APP_ICON_PATH)))
            except Exception:
                pass

        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry(f"480x360+{(sw - 480) // 2}+{(sh - 360) // 2}")

        ctk.CTkLabel(
            self,
            text="🛡️ Bem-vindo ao SentinelPC",
            font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"),
            text_color="#38BDF8",
        ).pack(padx=28, pady=(22, 6))

        ctk.CTkLabel(
            self,
            text=(
                "Antes de cadastrar o rosto do dono deste computador, defina a "
                "Senha Mestre da TI.\nEla será usada para abrir o painel administrativo "
                "e liberar a máquina em contingência (ex.: falha de webcam)."
            ),
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#CBD5E1",
            wraplength=420,
            justify="center",
        ).pack(padx=28, pady=(0, 18))

        self._pwd1 = ctk.CTkEntry(
            self,
            placeholder_text="Nova Senha Mestre da TI (mín. 4 caracteres)",
            show="•",
            width=360,
            height=36,
        )
        self._pwd1.pack(padx=28, pady=(0, 10))

        self._pwd2 = ctk.CTkEntry(
            self,
            placeholder_text="Confirme a Senha Mestre da TI",
            show="•",
            width=360,
            height=36,
        )
        self._pwd2.pack(padx=28, pady=(0, 8))
        self._pwd2.bind("<Return>", lambda _e: self._save())

        self._msg_lbl = ctk.CTkLabel(
            self,
            text="",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#F87171",
        )
        self._msg_lbl.pack(padx=28, pady=(0, 10))

        ctk.CTkButton(
            self,
            text="Salvar Senha e Configurar Rosto  ➔",
            width=360,
            height=40,
            fg_color="#0284C7",
            hover_color="#0369A1",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            command=self._save,
        ).pack(padx=28, pady=(4, 16))

        self.lift()
        self.focus_force()
        self._pwd1.focus_set()

    def _save(self) -> None:
        p1 = self._pwd1.get().strip()
        p2 = self._pwd2.get().strip()
        if len(p1) < 4:
            self._msg_lbl.configure(text="A senha deve ter no mínimo 4 caracteres.")
            return
        if p1 != p2:
            self._msg_lbl.configure(text="As senhas informadas não conferem.")
            return

        self.storage.set_admin_password(p1)
        # Ativa registro no logon do Windows por padrão conforme política configurada
        if self.storage.get_bool_setting("lock_on_startup", True):
            set_windows_startup_enabled(True)

        cb = self.on_completed
        self.destroy()
        cb()


class AdminPanelWindow:
    """Painel completo da TI para cadastro facial, auditoria de intrusos e políticas."""

    def __init__(
        self,
        master: ctk.CTk,
        storage: StorageManager,
        vision: BiometricVisionEngine,
        on_test_lock: Callable[[], None],
        on_closed: Callable[[], None] | None = None,
    ) -> None:
        self.master = master
        self.storage = storage
        self.vision = vision
        self.on_test_lock = on_test_lock
        self.on_closed = on_closed

        self.is_open = True
        self._cap: cv2.VideoCapture | None = None
        self._camera_running = False
        self._test_mode = False
        self._enrolling = False
        self._enroll_samples: list[np.ndarray] = []
        self._enroll_thumb: np.ndarray | None = None
        self._last_sample_time: float = 0.0
        self._selected_photo_path: str | None = None

        self._cam_w, self._cam_h = 384, 288
        blank_pil = Image.new("RGB", (self._cam_w, self._cam_h), (2, 6, 23))
        self._blank_cam_img = ctk.CTkImage(
            light_image=blank_pil, dark_image=blank_pil, size=(self._cam_w, self._cam_h)
        )
        blank_ev = Image.new("RGB", (360, 270), (15, 23, 42))
        self._blank_evidence_img = ctk.CTkImage(
            light_image=blank_ev, dark_image=blank_ev, size=(360, 270)
        )

        self.win = ctk.CTkToplevel(self.master)
        self.win.title(f"SentinelPC v{APP_VERSION} — Painel Administrativo da TI")
        self.win.geometry("960x640")
        self.win.minsize(900, 600)
        self.win.configure(fg_color="#0B1120")
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        if APP_ICON_PATH.exists():
            try:
                self.win.after(200, lambda: self.win.iconbitmap(str(APP_ICON_PATH)))
            except Exception:
                pass

        self.win.update_idletasks()
        sw = self.win.winfo_screenwidth()
        sh = self.win.winfo_screenheight()
        pos_y = max(15, (sh - 640) // 2 - 15)
        self.win.geometry(f"960x640+{max(15, (sw - 960) // 2)}+{pos_y}")

        self._build_layout()
        self._refresh_users_list()
        self._refresh_audit_list()
        self._start_preview_camera()

        self.win.lift()
        self.win.focus_force()

    def _build_layout(self) -> None:
        # Barra de Topo
        top = ctk.CTkFrame(self.win, fg_color="#0F172A", corner_radius=0, height=56)
        top.pack(fill="x", side="top")
        top.pack_propagate(False)

        ctk.CTkLabel(
            top,
            text="🛡️ SENTINEL PC — PAINEL DA TI",
            font=ctk.CTkFont(family="Segoe UI", size=16, weight="bold"),
            text_color="#38BDF8",
        ).pack(side="left", padx=20)

        self._protection_badge = ctk.CTkLabel(
            top,
            text="",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color="#4ADE80",
        )
        self._protection_badge.pack(side="left", padx=12)

        ctk.CTkButton(
            top,
            text="🔒 Bloquear e Testar Agora",
            width=190,
            height=34,
            fg_color="#0284C7",
            hover_color="#0369A1",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            command=self._trigger_test_lock,
        ).pack(side="right", padx=20, pady=11)

        # Abas Principais
        self.tabs = ctk.CTkTabview(
            self.win,
            fg_color="#0F172A",
            segmented_button_fg_color="#1E293B",
            segmented_button_selected_color="#0284C7",
            segmented_button_selected_hover_color="#0369A1",
        )
        self.tabs.pack(fill="both", expand=True, padx=16, pady=(8, 14))

        tab_users = self.tabs.add("👤 Dono do PC & Cadastro Facial")
        tab_audit = self.tabs.add("🚨 Auditoria & Fotos de Intrusos")
        tab_settings = self.tabs.add("⚙️ Políticas & Inicialização")

        self._build_users_tab(tab_users)
        self._build_audit_tab(tab_audit)
        self._build_settings_tab(tab_settings)

    # ------------------------------------------------------------------
    # ABA 1: Cadastro Facial e Teste de Prova de Vida
    # ------------------------------------------------------------------
    def _build_users_tab(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=5)
        parent.grid_columnconfigure(1, weight=4)
        parent.grid_rowconfigure(0, weight=1)

        # Coluna Esquerda: Webcam + Formulário de Cadastro / Simulador
        left = ctk.CTkScrollableFrame(parent, fg_color="#131C31", corner_radius=12)
        left.grid(row=0, column=0, sticky="nsew", padx=(6, 8), pady=6)

        ctk.CTkLabel(
            left,
            text="Captura Biométrica & Simulador",
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
            text_color="#F8FAFC",
        ).pack(anchor="w", padx=14, pady=(8, 6))

        cam_box = ctk.CTkFrame(
            left,
            fg_color="#020617",
            corner_radius=10,
            border_width=2,
            border_color="#1E293B",
            width=self._cam_w + 8,
            height=self._cam_h + 8,
        )
        cam_box.pack(padx=14, pady=4)
        cam_box.pack_propagate(False)

        self._preview_lbl = ctk.CTkLabel(
            cam_box,
            image=self._blank_cam_img,
            compound="center",
            text="Câmera desligada",
            text_color="#94A3B8",
        )
        self._preview_lbl.place(relx=0.5, rely=0.5, anchor="center")

        self._cam_status_lbl = ctk.CTkLabel(
            left,
            text="Pronto para capturar amostras do dono do computador.",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color="#38BDF8",
            wraplength=400,
        )
        self._cam_status_lbl.pack(padx=14, pady=(4, 6))

        cam_ctrl = ctk.CTkFrame(left, fg_color="transparent")
        cam_ctrl.pack(fill="x", padx=14, pady=(0, 8))

        self._toggle_cam_btn = ctk.CTkButton(
            cam_ctrl,
            text="⏹ Pausar Câmera",
            width=145,
            height=30,
            fg_color="#334155",
            hover_color="#475569",
            command=self._toggle_preview_camera,
        )
        self._toggle_cam_btn.pack(side="left", padx=(0, 8))

        self._sim_btn = ctk.CTkButton(
            cam_ctrl,
            text="🧪 Modo Teste (Piscar)",
            width=185,
            height=30,
            fg_color="#1E293B",
            hover_color="#334155",
            border_width=1,
            border_color="#38BDF8",
            command=self._toggle_test_mode,
        )
        self._sim_btn.pack(side="left")

        # Formulário de novo cadastro
        form = ctk.CTkFrame(left, fg_color="#0F172A", corner_radius=10)
        form.pack(fill="x", padx=14, pady=(4, 10))

        ctk.CTkLabel(
            form,
            text="Cadastrar Novo Rosto Autorizado:",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color="#E2E8F0",
        ).pack(anchor="w", padx=12, pady=(10, 6))

        inputs_row = ctk.CTkFrame(form, fg_color="transparent")
        inputs_row.pack(fill="x", padx=12, pady=(0, 8))

        self._name_entry = ctk.CTkEntry(
            inputs_row,
            placeholder_text="Nome do colaborador (ex: Maria Silva)",
            height=34,
        )
        self._name_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

        self._dept_entry = ctk.CTkEntry(
            inputs_row,
            placeholder_text="Setor / Cargo",
            width=135,
            height=34,
        )
        self._dept_entry.pack(side="left")

        self._enroll_btn = ctk.CTkButton(
            form,
            text="📸 Capturar 5 Amostras e Cadastrar Rosto",
            height=38,
            fg_color="#16A34A",
            hover_color="#15803D",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            command=self._begin_enrollment,
        )
        self._enroll_btn.pack(fill="x", padx=12, pady=(0, 12))

        # Coluna Direita: Lista de Usuários Autorizados neste PC
        right = ctk.CTkFrame(parent, fg_color="#131C31", corner_radius=12)
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 6), pady=6)

        ctk.CTkLabel(
            right,
            text="Rostos Autorizados neste Computador",
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
            text_color="#F8FAFC",
        ).pack(anchor="w", padx=16, pady=(14, 4))

        ctk.CTkLabel(
            right,
            text="Apenas as pessoas listadas abaixo conseguirão desbloquear a estação.",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#94A3B8",
            wraplength=350,
            justify="left",
        ).pack(anchor="w", padx=16, pady=(0, 8))

        self._users_scroll = ctk.CTkScrollableFrame(
            right, fg_color="#0F172A", corner_radius=10
        )
        self._users_scroll.pack(fill="both", expand=True, padx=14, pady=(0, 14))

    # ------------------------------------------------------------------
    # ABA 2: Auditoria & Fotos de Intrusos
    # ------------------------------------------------------------------
    def _build_audit_tab(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=5)
        parent.grid_columnconfigure(1, weight=4)
        parent.grid_rowconfigure(1, weight=1)

        toolbar = ctk.CTkFrame(parent, fg_color="transparent")
        toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", padx=8, pady=(4, 6))

        self._only_alerts_var = ctk.BooleanVar(value=True)
        ctk.CTkSwitch(
            toolbar,
            text="Exibir apenas alertas e intrusos",
            variable=self._only_alerts_var,
            command=self._refresh_audit_list,
            progress_color="#EF4444",
        ).pack(side="left", padx=8)

        ctk.CTkButton(
            toolbar,
            text="🔄 Atualizar",
            width=110,
            height=30,
            fg_color="#334155",
            hover_color="#475569",
            command=self._refresh_audit_list,
        ).pack(side="left", padx=6)

        ctk.CTkButton(
            toolbar,
            text="📂 Abrir Pasta de Fotos",
            width=165,
            height=30,
            fg_color="#0284C7",
            hover_color="#0369A1",
            command=lambda: os.startfile(str(get_intruders_dir())),
        ).pack(side="left", padx=6)

        ctk.CTkButton(
            toolbar,
            text="🗑️ Limpar Registros",
            width=145,
            height=30,
            fg_color="#7F1D1D",
            hover_color="#991B1B",
            command=self._clear_logs_action,
        ).pack(side="right", padx=8)

        # Lista de Eventos
        self._audit_scroll = ctk.CTkScrollableFrame(
            parent, fg_color="#131C31", corner_radius=12
        )
        self._audit_scroll.grid(row=1, column=0, sticky="nsew", padx=(6, 8), pady=6)

        # Visualizador da Foto Capturada
        viewer = ctk.CTkFrame(parent, fg_color="#131C31", corner_radius=12)
        viewer.grid(row=1, column=1, sticky="nsew", padx=(8, 6), pady=6)

        ctk.CTkLabel(
            viewer,
            text="Evidência Fotográfica",
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
            text_color="#F8FAFC",
        ).pack(anchor="w", padx=16, pady=(14, 8))

        self._evidence_img_lbl = ctk.CTkLabel(
            viewer,
            image=self._blank_evidence_img,
            compound="center",
            text="Selecione um alerta ao lado\npara inspecionar a foto capturada",
            text_color="#94A3B8",
        )
        self._evidence_img_lbl.pack(padx=16, pady=6)

        self._evidence_meta_lbl = ctk.CTkLabel(
            viewer,
            text="",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#CBD5E1",
            wraplength=350,
            justify="left",
        )
        self._evidence_meta_lbl.pack(anchor="w", padx=16, pady=(6, 8))

        self._open_photo_btn = ctk.CTkButton(
            viewer,
            text="🔍 Abrir Imagem em Tamanho Real",
            height=34,
            fg_color="#334155",
            hover_color="#475569",
            state="disabled",
            command=self._open_selected_photo_external,
        )
        self._open_photo_btn.pack(fill="x", padx=16, pady=(4, 14))

    # ------------------------------------------------------------------
    # ABA 3: Políticas de Segurança & Inicialização do Windows
    # ------------------------------------------------------------------
    def _build_settings_tab(self, parent: ctk.CTkFrame) -> None:
        scroll = ctk.CTkScrollableFrame(parent, fg_color="#131C31", corner_radius=12)
        scroll.pack(fill="both", expand=True, padx=8, pady=6)

        ctk.CTkLabel(
            scroll,
            text="Gatilhos de Bloqueio e Prova de Vida",
            font=ctk.CTkFont(family="Segoe UI", size=16, weight="bold"),
            text_color="#F8FAFC",
        ).pack(anchor="w", padx=18, pady=(12, 10))

        self._var_startup = ctk.BooleanVar(
            value=self.storage.get_bool_setting("lock_on_startup", True)
            and is_windows_startup_enabled()
        )
        ctk.CTkSwitch(
            scroll,
            text="Iniciar com o Windows e bloquear imediatamente no logon",
            variable=self._var_startup,
            command=self._save_policy_settings,
            font=ctk.CTkFont(family="Segoe UI", size=13),
        ).pack(anchor="w", padx=20, pady=8)

        self._var_unlock = ctk.BooleanVar(
            value=self.storage.get_bool_setting("lock_on_unlock", True)
        )
        ctk.CTkSwitch(
            scroll,
            text="Exigir reconhecimento facial ao desbloquear a tela (Win+L) ou voltar de suspensão",
            variable=self._var_unlock,
            command=self._save_policy_settings,
            font=ctk.CTkFont(family="Segoe UI", size=13),
        ).pack(anchor="w", padx=20, pady=8)

        self._var_liveness = ctk.BooleanVar(
            value=self.storage.get_bool_setting("require_liveness", True)
        )
        ctk.CTkSwitch(
            scroll,
            text="Exigir Prova de Vida rápida (piscar os olhos) contra fotos no celular",
            variable=self._var_liveness,
            command=self._save_policy_settings,
            font=ctk.CTkFont(family="Segoe UI", size=13),
        ).pack(anchor="w", padx=20, pady=8)

        # Rigor Biométrico & Câmera
        sep1 = ctk.CTkFrame(scroll, fg_color="#0F172A", corner_radius=10)
        sep1.pack(fill="x", padx=18, pady=12)

        curr_thresh = self.storage.get_float_setting("similarity_threshold", 0.58)
        self._thresh_lbl = ctk.CTkLabel(
            sep1,
            text=f"Rigor da Comparação Facial: {curr_thresh:.2f} (Recomendado: 0.58)",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color="#E2E8F0",
        )
        self._thresh_lbl.pack(anchor="w", padx=16, pady=(12, 4))

        self._thresh_slider = ctk.CTkSlider(
            sep1,
            from_=0.50,
            to=0.74,
            number_of_steps=24,
            width=360,
            command=self._on_threshold_slide,
        )
        self._thresh_slider.set(curr_thresh)
        self._thresh_slider.pack(anchor="w", padx=16, pady=(0, 10))

        cam_row = ctk.CTkFrame(sep1, fg_color="transparent")
        cam_row.pack(fill="x", padx=16, pady=(2, 12))

        ctk.CTkLabel(
            cam_row,
            text="Índice da Webcam:",
            font=ctk.CTkFont(family="Segoe UI", size=13),
            text_color="#CBD5E1",
        ).pack(side="left", padx=(0, 10))

        curr_cam = str(self.storage.get_int_setting("camera_index", 0))
        self._cam_menu = ctk.CTkOptionMenu(
            cam_row,
            values=["0", "1", "2"],
            width=90,
            command=self._on_camera_changed,
        )
        self._cam_menu.set(curr_cam)
        self._cam_menu.pack(side="left", padx=(0, 20))

        ctk.CTkButton(
            cam_row,
            text="📂 Abrir Pasta Local de Dados",
            width=210,
            height=30,
            fg_color="#334155",
            hover_color="#475569",
            command=lambda: os.startfile(str(get_data_dir())),
        ).pack(side="left")

        # Alteração de Senha Mestre da TI
        pwd_card = ctk.CTkFrame(scroll, fg_color="#0F172A", corner_radius=10)
        pwd_card.pack(fill="x", padx=18, pady=(4, 14))

        ctk.CTkLabel(
            pwd_card,
            text="🔑 Alterar Senha Mestre da TI",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            text_color="#F8FAFC",
        ).pack(anchor="w", padx=16, pady=(12, 8))

        p_row = ctk.CTkFrame(pwd_card, fg_color="transparent")
        p_row.pack(fill="x", padx=16, pady=(0, 10))

        self._old_pwd = ctk.CTkEntry(
            p_row, placeholder_text="Senha atual", show="•", width=180, height=34
        )
        self._old_pwd.pack(side="left", padx=(0, 8))

        self._new_pwd = ctk.CTkEntry(
            p_row, placeholder_text="Nova senha (mín. 4)", show="•", width=180, height=34
        )
        self._new_pwd.pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            p_row,
            text="Atualizar Senha",
            width=145,
            height=34,
            fg_color="#0284C7",
            hover_color="#0369A1",
            command=self._change_password_action,
        ).pack(side="left")

        self._pwd_change_msg = ctk.CTkLabel(
            pwd_card,
            text="",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#4ADE80",
        )
        self._pwd_change_msg.pack(anchor="w", padx=16, pady=(0, 10))

    # ------------------------------------------------------------------
    # Lógica de Câmera, Cadastro e Simulador
    # ------------------------------------------------------------------
    def _start_preview_camera(self) -> None:
        if self._camera_running:
            return
        cam_idx = self.storage.get_int_setting("camera_index", 0)
        self._cap = open_fast_camera(cam_idx, 640, 480)
        self._camera_running = True
        self._toggle_cam_btn.configure(text="⏹ Pausar Câmera")
        self._tick_preview()

    def _stop_preview_camera(self) -> None:
        self._camera_running = False
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None
        if self.is_open:
            self._preview_lbl.configure(
                image=self._blank_cam_img, text="Câmera em pausa"
            )
            self._toggle_cam_btn.configure(text="🎥 Ligar Câmera")

    def _toggle_preview_camera(self) -> None:
        if self._camera_running:
            self._stop_preview_camera()
        else:
            self._start_preview_camera()

    def _toggle_test_mode(self) -> None:
        self._test_mode = not self._test_mode
        self.vision.reset_tracker()
        if self._test_mode:
            if not self._camera_running:
                self._start_preview_camera()
            self._sim_btn.configure(
                text="⏹ Encerrar Modo Teste", fg_color="#0284C7"
            )
            self._cam_status_lbl.configure(
                text="Modo Teste ativo: olhe para a câmera e pisque para testar!",
                text_color="#FCD34D",
            )
        else:
            self._sim_btn.configure(
                text="🧪 Modo Teste (Piscar)", fg_color="#1E293B"
            )
            self._cam_status_lbl.configure(
                text="Pronto para capturar amostras do dono do computador.",
                text_color="#38BDF8",
            )

    def _begin_enrollment(self) -> None:
        name = self._name_entry.get().strip()
        if not name:
            self._cam_status_lbl.configure(
                text="⚠️ Digite o nome do colaborador antes de capturar o rosto!",
                text_color="#F87171",
            )
            self._name_entry.focus_set()
            return

        if not self._camera_running:
            self._start_preview_camera()

        self._test_mode = False
        self._sim_btn.configure(text="🧪 Modo Teste (Piscar)", fg_color="#1E293B")
        self._enrolling = True
        self._enroll_samples = []
        self._enroll_thumb = None
        self._last_sample_time = 0.0
        self._enroll_btn.configure(state="disabled", text="Capturando 0/5...")
        self._cam_status_lbl.configure(
            text="Olhe para a câmera e mova levemente o rosto (0/5 amostras)...",
            text_color="#FCD34D",
        )

    def _tick_preview(self) -> None:
        if not self.is_open or not self._camera_running:
            return

        if self._cap is None or not self._cap.isOpened():
            self._preview_lbl.configure(
                image=self._blank_cam_img,
                text="⚠️ Webcam não encontrada neste índice.\nVerifique na aba Políticas & Inicialização.",
            )
            self.win.after(600, self._tick_preview)
            return

        ok, frame = self._cap.read()
        if not ok or frame is None or frame.size == 0:
            self.win.after(100, self._tick_preview)
            return

        vis_bgr = cv2.flip(frame, 1)

        if self._enrolling:
            valid, msg, emb, bbox = self.vision.evaluate_enrollment_sample(frame)
            if bbox is not None:
                bx, by, bw, bh = bbox
                mx = vis_bgr.shape[1] - bx - bw
                color = (80, 220, 100) if valid else (60, 180, 250)
                cv2.rectangle(vis_bgr, (mx, by), (mx + bw, by + bh), color, 2)

            now = time.monotonic()
            if valid and emb is not None and (now - self._last_sample_time) >= 0.42:
                self._last_sample_time = now
                self._enroll_samples.append(emb)
                if self._enroll_thumb is None and bbox is not None:
                    bx, by, bw, bh = bbox
                    fh, fw = frame.shape[:2]
                    pad = 24
                    self._enroll_thumb = frame[
                        max(0, by - pad) : min(fh, by + bh + pad),
                        max(0, bx - pad) : min(fw, bx + bw + pad),
                    ].copy()

                count = len(self._enroll_samples)
                self._enroll_btn.configure(text=f"Capturando {count}/5...")
                self._cam_status_lbl.configure(
                    text=f"✅ Amostra {count}/5 capturada! Mantenha o rosto visível...",
                    text_color="#4ADE80",
                )

                if count >= 5:
                    self._finish_enrollment()
            elif not valid:
                self._cam_status_lbl.configure(text=msg, text_color="#FCD34D")

        elif self._test_mode:
            users = self.storage.list_users(only_active=True)
            thresh = self.storage.get_float_setting("similarity_threshold", 0.58)
            req_live = self.storage.get_bool_setting("require_liveness", True)
            res = self.vision.analyze_frame(frame, users, thresh, req_live)
            vis_bgr = draw_hud_overlay(frame, res, mirror=True)
            if res.authenticated and res.matched_user is not None:
                self._cam_status_lbl.configure(
                    text=f"✅ TESTE APROVADO: {res.matched_user.name} (Score {res.best_score:.2f} | EAR {res.ear:.2f})",
                    text_color="#4ADE80",
                )
            elif res.matched_user is not None:
                self._cam_status_lbl.configure(
                    text=f"{res.status_message} (Score {res.best_score:.2f} | EAR {res.ear:.2f})",
                    text_color="#FCD34D",
                )
            else:
                self._cam_status_lbl.configure(
                    text=f"{res.status_message} (Melhor score: {res.best_score:.2f})",
                    text_color="#F87171" if res.face_found else "#38BDF8",
                )
        else:
            faces = self.vision.detect_faces(frame)
            if faces is not None and len(faces) > 0:
                bx, by, bw, bh = [int(v) for v in faces[0][:4]]
                mx = vis_bgr.shape[1] - bx - bw
                cv2.rectangle(vis_bgr, (mx, by), (mx + bw, by + bh), (235, 180, 40), 2)

        rgb = cv2.cvtColor(vis_bgr, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(rgb)
        ctk_img = ctk.CTkImage(
            light_image=pil_img, dark_image=pil_img, size=(self._cam_w, self._cam_h)
        )
        self._preview_lbl.configure(image=ctk_img, text="")
        self._preview_lbl._current_image = ctk_img

        self.win.after(35, self._tick_preview)

    def _finish_enrollment(self) -> None:
        self._enrolling = False
        name = self._name_entry.get().strip()
        dept = self._dept_entry.get().strip()
        try:
            self.storage.enroll_user(
                name=name,
                department=dept,
                embeddings=self._enroll_samples,
                thumbnail_bgr=self._enroll_thumb,
            )
            self._name_entry.delete(0, "end")
            self._dept_entry.delete(0, "end")
            self._cam_status_lbl.configure(
                text=f"🎉 Rosto de '{name}' cadastrado com sucesso (5 amostras biométricas)!",
                text_color="#4ADE80",
            )
            self._refresh_users_list()
        except Exception as exc:
            self._cam_status_lbl.configure(
                text=f"Erro ao salvar cadastro: {exc}", text_color="#F87171"
            )
        finally:
            self._enroll_samples = []
            self._enroll_btn.configure(
                state="normal", text="📸 Capturar 5 Amostras e Cadastrar Rosto"
            )

    # ------------------------------------------------------------------
    # Atualização das Listas (Usuários & Auditoria)
    # ------------------------------------------------------------------
    def _refresh_users_list(self) -> None:
        for child in self._users_scroll.winfo_children():
            child.destroy()

        users: list[EnrolledUser] = self.storage.list_users(only_active=False)
        if not users:
            self._protection_badge.configure(
                text="⚠️ Nenhum rosto cadastrado — Bloqueio aguardando cadastro",
                text_color="#FBBF24",
            )
            ctk.CTkLabel(
                self._users_scroll,
                text="Nenhum usuário cadastrado ainda.\nUse o painel ao lado para cadastrar o dono deste PC.",
                font=ctk.CTkFont(family="Segoe UI", size=13),
                text_color="#64748B",
                justify="center",
            ).pack(pady=40)
            return

        self._protection_badge.configure(
            text=f"✅ Proteção ativa ({len(users)} usuário(s) autorizado(s))",
            text_color="#4ADE80",
        )

        for user in users:
            card = ctk.CTkFrame(self._users_scroll, fg_color="#1E293B", corner_radius=8)
            card.pack(fill="x", padx=6, pady=5)

            if user.thumbnail_path and Path(user.thumbnail_path).exists():
                try:
                    pil_t = Image.open(user.thumbnail_path).convert("RGB")
                    ctk_t = ctk.CTkImage(light_image=pil_t, dark_image=pil_t, size=(46, 46))
                    lbl_t = ctk.CTkLabel(card, image=ctk_t, text="")
                    lbl_t._img_ref = ctk_t
                    lbl_t.pack(side="left", padx=10, pady=8)
                except Exception:
                    pass

            info = ctk.CTkFrame(card, fg_color="transparent")
            info.pack(side="left", fill="x", expand=True, padx=6, pady=8)

            dept_suffix = f"  •  {user.department}" if user.department else ""
            ctk.CTkLabel(
                info,
                text=f"{user.name}{dept_suffix}",
                font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
                text_color="#F8FAFC",
            ).pack(anchor="w")

            ctk.CTkLabel(
                info,
                text=f"{len(user.embeddings)} amostras faciais  |  Desde {user.created_at}",
                font=ctk.CTkFont(family="Segoe UI", size=11),
                text_color="#94A3B8",
            ).pack(anchor="w")

            ctk.CTkButton(
                card,
                text="Remover",
                width=78,
                height=28,
                fg_color="#7F1D1D",
                hover_color="#991B1B",
                font=ctk.CTkFont(family="Segoe UI", size=11),
                command=lambda uid=user.id: self._remove_user_action(uid),
            ).pack(side="right", padx=10)

    def _remove_user_action(self, user_id: int) -> None:
        self.storage.delete_user(user_id)
        self._refresh_users_list()

    def _refresh_audit_list(self) -> None:
        for child in self._audit_scroll.winfo_children():
            child.destroy()

        records: list[AuditRecord] = self.storage.list_audit_logs(
            limit=80, only_alerts=self._only_alerts_var.get()
        )
        if not records:
            ctk.CTkLabel(
                self._audit_scroll,
                text="Nenhum evento registrado neste filtro.",
                font=ctk.CTkFont(family="Segoe UI", size=13),
                text_color="#64748B",
            ).pack(pady=40)
            return

        badge_map = {
            "INTRUSO_DETECTADO": ("🚨 INTRUSO DETECTADO", "#EF4444"),
            "FALHA_PROVA_VIDA": ("⚠️ FALHA PROVA DE VIDA", "#F59E0B"),
            "SENHA_TI_INVALIDA": ("🔑 SENHA TI INVÁLIDA", "#F87171"),
            "ACESSO_LIBERADO": ("✅ ACESSO LIBERADO", "#22C55E"),
            "CONTINGENCIA_TI": ("🛡️ CONTINGÊNCIA TI", "#38BDF8"),
        }

        for rec in records:
            title_txt, badge_color = badge_map.get(
                rec.event_type, (rec.event_type, "#94A3B8")
            )
            item = ctk.CTkFrame(self._audit_scroll, fg_color="#1E293B", corner_radius=8)
            item.pack(fill="x", padx=6, pady=4)

            top_r = ctk.CTkFrame(item, fg_color="transparent")
            top_r.pack(fill="x", padx=10, pady=(6, 2))

            ctk.CTkLabel(
                top_r,
                text=title_txt,
                font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
                text_color=badge_color,
            ).pack(side="left")

            ctk.CTkLabel(
                top_r,
                text=rec.timestamp,
                font=ctk.CTkFont(family="Segoe UI", size=11),
                text_color="#CBD5E1",
            ).pack(side="right")

            sub_txt = f"Usuário: {rec.person_label or '-'}  |  Score: {rec.similarity_score:.2f}  |  {rec.details}"
            ctk.CTkLabel(
                item,
                text=sub_txt,
                font=ctk.CTkFont(family="Segoe UI", size=11),
                text_color="#94A3B8",
                wraplength=390,
                justify="left",
            ).pack(anchor="w", padx=10, pady=(0, 6))

            if rec.photo_path and Path(rec.photo_path).exists():
                ctk.CTkButton(
                    item,
                    text="📷 Ver Foto do Evento",
                    height=26,
                    fg_color="#0284C7",
                    hover_color="#0369A1",
                    font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
                    command=lambda r=rec: self._select_audit_photo(r),
                ).pack(anchor="e", padx=10, pady=(0, 8))

    def _select_audit_photo(self, rec: AuditRecord) -> None:
        if not rec.photo_path or not Path(rec.photo_path).exists():
            return
        try:
            pil_ev = Image.open(rec.photo_path).convert("RGB")
            ctk_ev = ctk.CTkImage(light_image=pil_ev, dark_image=pil_ev, size=(360, 270))
            self._evidence_img_lbl.configure(image=ctk_ev, text="")
            self._evidence_img_lbl._ev_ref = ctk_ev
            self._selected_photo_path = rec.photo_path
            self._open_photo_btn.configure(state="normal")
            self._evidence_meta_lbl.configure(
                text=(
                    f"Data/Hora: {rec.timestamp}\n"
                    f"Evento: {rec.event_type}\n"
                    f"Estação: {rec.computer_name} ({rec.windows_user})\n"
                    f"Detalhes: {rec.details}"
                )
            )
        except Exception as exc:
            self._evidence_meta_lbl.configure(text=f"Erro ao carregar foto: {exc}")

    def _open_selected_photo_external(self) -> None:
        if self._selected_photo_path and Path(self._selected_photo_path).exists():
            os.startfile(self._selected_photo_path)

    def _clear_logs_action(self) -> None:
        self.storage.clear_audit_logs(delete_photos=True)
        self._selected_photo_path = None
        self._open_photo_btn.configure(state="disabled")
        self._evidence_img_lbl.configure(
            image=self._blank_evidence_img,
            text="Histórico de auditoria limpo.",
        )
        self._evidence_meta_lbl.configure(text="")
        self._refresh_audit_list()

    # ------------------------------------------------------------------
    # Configurações & Políticas
    # ------------------------------------------------------------------
    def _save_policy_settings(self) -> None:
        startup_on = bool(self._var_startup.get())
        self.storage.set_setting("lock_on_startup", startup_on)
        set_windows_startup_enabled(startup_on)
        self.storage.set_setting("lock_on_unlock", bool(self._var_unlock.get()))
        self.storage.set_setting("require_liveness", bool(self._var_liveness.get()))

    def _on_threshold_slide(self, value: float) -> None:
        rounded = round(float(value), 2)
        self._thresh_lbl.configure(
            text=f"Rigor da Comparação Facial: {rounded:.2f} (Recomendado: 0.58)"
        )
        self.storage.set_setting("similarity_threshold", f"{rounded:.2f}")

    def _on_camera_changed(self, value: str) -> None:
        self.storage.set_setting("camera_index", int(value))
        if self._camera_running:
            self._stop_preview_camera()
            self._start_preview_camera()

    def _change_password_action(self) -> None:
        old_p = self._old_pwd.get()
        new_p = self._new_pwd.get().strip()
        if not self.storage.verify_admin_password(old_p):
            self._pwd_change_msg.configure(
                text="❌ Senha atual incorreta.", text_color="#F87171"
            )
            return
        if len(new_p) < 4:
            self._pwd_change_msg.configure(
                text="⚠️ A nova senha deve ter pelo menos 4 caracteres.",
                text_color="#FBBF24",
            )
            return
        self.storage.set_admin_password(new_p)
        self._old_pwd.delete(0, "end")
        self._new_pwd.delete(0, "end")
        self._pwd_change_msg.configure(
            text="✅ Senha Mestre da TI alterada com sucesso!", text_color="#4ADE80"
        )

    def _trigger_test_lock(self) -> None:
        if not self.storage.has_enrolled_users():
            self.tabs.set("👤 Dono do PC & Cadastro Facial")
            self._cam_status_lbl.configure(
                text="⚠️ Cadastre pelo menos um rosto antes de ativar a tela de bloqueio!",
                text_color="#FBBF24",
            )
            return
        cb = self.on_test_lock
        self.close()
        cb()

    def close(self) -> None:
        if not self.is_open:
            return
        self.is_open = False
        self._stop_preview_camera()
        try:
            self.win.destroy()
        except Exception:
            pass
        if self.on_closed is not None:
            self.on_closed()
