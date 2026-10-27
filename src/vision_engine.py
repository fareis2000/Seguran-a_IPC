"""Motor biométrico de detecção facial (YuNet), reconhecimento rigoroso (SFace 128D
por consenso de centroide + múltiplas amostras + janela temporal) e prova de vida
anti-fraude por piscada (InsightFace 2d106det EAR adaptativo)."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import math
import time
from typing import Sequence

import cv2
import numpy as np

from src.paths import LANDMARK_106_MODEL_PATH, SFACE_MODEL_PATH, YUNET_MODEL_PATH
from src.storage import EnrolledUser


def open_fast_camera(camera_index: int = 0, width: int = 640, height: int = 480) -> cv2.VideoCapture:
    """Abre a webcam rapidamente no Windows priorizando DirectShow (CAP_DSHOW)."""
    cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap.release()
        cap = cv2.VideoCapture(camera_index)
    if cap.isOpened():
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        cap.set(cv2.CAP_PROP_FPS, 30)
    return cap


@dataclass
class FaceAnalysisResult:
    face_found: bool = False
    num_faces: int = 0
    bbox: tuple[int, int, int, int] | None = None
    raw_face: np.ndarray | None = None
    embedding: np.ndarray | None = None
    matched_user: EnrolledUser | None = None
    best_score: float = 0.0
    ear: float = 0.0
    right_eye_contour: np.ndarray | None = None
    left_eye_contour: np.ndarray | None = None
    liveness_passed: bool = False
    liveness_stage: str = "SEM_ROSTO"
    status_message: str = "Posicione o rosto em frente à câmera"
    quality_ok: bool = False
    authenticated: bool = False


@dataclass
class LivenessTrackerState:
    """Rastreia continuidade espacial, histórico temporal de scores e piscada real."""

    last_center: tuple[float, float] | None = None
    last_size: float = 0.0
    missed_frames: int = 0
    matched_user_id: int | None = None
    score_history: deque[float] = field(default_factory=lambda: deque(maxlen=6))
    open_ear_baseline: float = 0.0
    open_frames_count: int = 0
    blink_closed_frames: int = 0
    saw_blink_dip: bool = False
    reopen_verified_frames: int = 0
    liveness_confirmed: bool = False
    track_started_at: float = field(default_factory=time.monotonic)
    unauthorized_since: float | None = None

    def reset(self) -> None:
        self.last_center = None
        self.last_size = 0.0
        self.missed_frames = 0
        self.matched_user_id = None
        self.score_history.clear()
        self.open_ear_baseline = 0.0
        self.open_frames_count = 0
        self.blink_closed_frames = 0
        self.saw_blink_dip = False
        self.reopen_verified_frames = 0
        self.liveness_confirmed = False
        self.track_started_at = time.monotonic()
        self.unauthorized_since = None

    def reset_identity(self) -> None:
        self.matched_user_id = None
        self.score_history.clear()
        self.open_ear_baseline = 0.0
        self.open_frames_count = 0
        self.blink_closed_frames = 0
        self.saw_blink_dip = False
        self.reopen_verified_frames = 0
        self.liveness_confirmed = False


class BiometricVisionEngine:
    """Executa detecção, consenso biométrico 128D e prova de vida 106-pontos em CPU."""

    def __init__(self) -> None:
        if not YUNET_MODEL_PATH.exists():
            raise FileNotFoundError(f"Modelo YuNet ausente: {YUNET_MODEL_PATH}")
        if not SFACE_MODEL_PATH.exists():
            raise FileNotFoundError(f"Modelo SFace ausente: {SFACE_MODEL_PATH}")
        if not LANDMARK_106_MODEL_PATH.exists():
            raise FileNotFoundError(f"Modelo 2d106det ausente: {LANDMARK_106_MODEL_PATH}")

        # Carrega modelos em memória via bytes (100% compatível com caminhos Windows com acentos)
        det_buf = np.frombuffer(YUNET_MODEL_PATH.read_bytes(), dtype=np.uint8)
        rec_buf = np.frombuffer(SFACE_MODEL_PATH.read_bytes(), dtype=np.uint8)
        lm_buf = np.frombuffer(LANDMARK_106_MODEL_PATH.read_bytes(), dtype=np.uint8)
        empty_cfg = np.empty(0, dtype=np.uint8)

        self._detector = cv2.FaceDetectorYN.create(
            "onnx",
            det_buf,
            empty_cfg,
            (320, 320),
            0.82,
            0.3,
            5000,
        )
        self._recognizer = cv2.FaceRecognizerSF.create("onnx", rec_buf, empty_cfg)
        self._landmark_net = cv2.dnn.readNetFromONNX(lm_buf)
        self.tracker = LivenessTrackerState()

    def reset_tracker(self) -> None:
        self.tracker.reset()

    def detect_faces(self, frame_bgr: np.ndarray) -> np.ndarray | None:
        """Detecta rostos na imagem retornando array Nx15 no espaço de coordenadas original."""
        h, w = frame_bgr.shape[:2]
        max_dim = 640
        if max(w, h) > max_dim:
            scale = max_dim / float(max(w, h))
            small_w, small_h = int(w * scale), int(h * scale)
            resized = cv2.resize(frame_bgr, (small_w, small_h), interpolation=cv2.INTER_LINEAR)
        else:
            scale = 1.0
            small_w, small_h = w, h
            resized = frame_bgr

        self._detector.setInputSize((small_w, small_h))
        _, faces = self._detector.detect(resized)
        if faces is None or len(faces) == 0:
            return None

        faces = faces.copy()
        if scale != 1.0:
            faces[:, :14] /= scale

        areas = faces[:, 2] * faces[:, 3]
        order = np.argsort(-areas)
        return faces[order]

    @staticmethod
    def _is_frontal_pose(raw_face: np.ndarray) -> tuple[bool, str]:
        """Rejeita perfis laterais extremos ou cabeças muito inclinadas que distorcem o alinhamento."""
        bw = float(raw_face[2])
        if bw < 1.0:
            return False, "Aproxime-se da câmera"

        r_eye = raw_face[4:6].astype(np.float64)
        l_eye = raw_face[6:8].astype(np.float64)
        nose = raw_face[8:10].astype(np.float64)

        eye_vec = l_eye - r_eye
        eye_dist = float(np.hypot(eye_vec[0], eye_vec[1]))
        if eye_dist < bw * 0.21:
            return False, "Olhe de frente para a câmera"

        roll_deg = abs(math.degrees(math.atan2(eye_vec[1], eye_vec[0])))
        if roll_deg > 26.0:
            return False, "Mantenha a cabeça alinhada de frente para a câmera"

        # Projeção horizontal do nariz entre o olho direito (0.0) e esquerdo (1.0)
        nose_rel = float(np.dot(nose - r_eye, eye_vec) / (eye_dist * eye_dist + 1e-6))
        if nose_rel < 0.18 or nose_rel > 0.82:
            return False, "Vire o rosto de frente para a câmera"

        return True, ""

    def extract_embedding(self, frame_bgr: np.ndarray, raw_face: np.ndarray) -> np.ndarray:
        """Alinha o recorte facial pelos 5 pontos do YuNet e extrai vetor SFace 128-D."""
        aligned = self._recognizer.alignCrop(frame_bgr, raw_face)
        feat = self._recognizer.feature(aligned)
        return np.asarray(feat, dtype=np.float32).reshape(1, -1).copy()

    def compute_ear_and_contours(
        self, frame_bgr: np.ndarray, bbox: tuple[int, int, int, int]
    ) -> tuple[float, np.ndarray, np.ndarray]:
        """Extrai 106 landmarks faciais e calcula o Eye Aspect Ratio (EAR) de ambos os olhos."""
        x, y, bw, bh = bbox
        cx = x + bw * 0.5
        cy = y + bh * 0.5
        scale = 192.0 / (max(bw, bh) * 1.5 + 1e-6)

        m_mat = np.array(
            [
                [scale, 0.0, 96.0 - cx * scale],
                [0.0, scale, 96.0 - cy * scale],
            ],
            dtype=np.float32,
        )
        crop = cv2.warpAffine(frame_bgr, m_mat, (192, 192), flags=cv2.INTER_LINEAR)
        blob = cv2.dnn.blobFromImage(crop, 1.0, (192, 192), (0.0, 0.0, 0.0), swapRB=True)
        self._landmark_net.setInput(blob)
        pred = self._landmark_net.forward()[0].reshape(106, 2)
        pts_crop = (pred + 1.0) * 96.0
        pts_img = (pts_crop - m_mat[:, 2]) / scale

        right_eye = pts_img[33:43]
        left_eye = pts_img[87:97]

        ear_r = self._single_eye_ear_106(right_eye)
        ear_l = self._single_eye_ear_106(left_eye)
        ear = float((ear_r + ear_l) * 0.5)

        poly_idx = [2, 8, 7, 9, 6, 4, 0, 3]
        r_poly = right_eye[poly_idx].astype(np.int32)
        l_poly = left_eye[poly_idx].astype(np.int32)
        return ear, r_poly, l_poly

    @staticmethod
    def _single_eye_ear_106(eye_pts: np.ndarray) -> float:
        horiz = float(np.linalg.norm(eye_pts[6] - eye_pts[2]))
        if horiz < 1e-4:
            return 0.0
        v1 = float(np.linalg.norm(eye_pts[8] - eye_pts[3]))
        v2 = float(np.linalg.norm(eye_pts[7] - eye_pts[0]))
        v3 = float(np.linalg.norm(eye_pts[9] - eye_pts[4]))
        return (v1 + v2 + v3) / (3.0 * horiz)

    @staticmethod
    def _l2_normalize(vec: np.ndarray) -> np.ndarray:
        flat = np.asarray(vec, dtype=np.float32).ravel()
        norm = float(np.linalg.norm(flat))
        if norm < 1e-8:
            return flat
        return flat / norm

    def match_user(
        self,
        embedding: np.ndarray,
        users: Sequence[EnrolledUser],
        threshold: float,
    ) -> tuple[EnrolledUser | None, float]:
        """Compara o vetor contra os usuários ativos exigindo consenso entre:
        1) Similaridade com o centroide médio L2 do usuário;
        2) Média das Top-3 amostras cadastradas (impede picos isolados em 1 amostra);
        3) Quantidade mínima de amostras concordantes quando o cadastro tem >= 3 amostras.
        """
        cand = self._l2_normalize(embedding)
        best_consensus = -1.0
        best_user: EnrolledUser | None = None
        best_passed_all = False

        for user in users:
            if not user.active or not user.embeddings:
                continue

            ref_norms = [self._l2_normalize(e) for e in user.embeddings]
            indiv_scores = sorted(
                (float(np.dot(cand, r)) for r in ref_norms), reverse=True
            )

            centroid = np.mean(ref_norms, axis=0)
            c_norm = float(np.linalg.norm(centroid))
            if c_norm > 1e-8:
                centroid = centroid / c_norm
            centroid_score = float(np.dot(cand, centroid))

            k_top = min(3, len(indiv_scores))
            top_k_mean = float(sum(indiv_scores[:k_top]) / k_top)
            consensus = 0.50 * centroid_score + 0.50 * top_k_mean

            # Exige que múltiplas amostras concordem (evita falso-positivo por 1 amostra ruidosa)
            min_sample_floor = threshold - 0.05
            if len(indiv_scores) >= 3:
                samples_ok = sum(1 for s in indiv_scores if s >= min_sample_floor) >= 3
            else:
                samples_ok = indiv_scores[-1] >= min_sample_floor

            user_passed = (
                consensus >= threshold
                and centroid_score >= (threshold - 0.03)
                and samples_ok
            )

            if consensus > best_consensus:
                best_consensus = consensus
                best_user = user
                best_passed_all = user_passed

        clamped_score = max(0.0, best_consensus)
        if best_user is not None and best_passed_all:
            return best_user, clamped_score
        return None, clamped_score

    def evaluate_enrollment_sample(
        self, frame_bgr: np.ndarray
    ) -> tuple[bool, str, np.ndarray | None, tuple[int, int, int, int] | None]:
        """Valida qualidade rigorosa da amostra para cadastro (frontal, iluminada e olhos abertos)."""
        faces = self.detect_faces(frame_bgr)
        if faces is None or len(faces) == 0:
            return False, "Nenhum rosto detectado. Olhe para a câmera.", None, None
        if len(faces) > 1:
            return False, "Mantenha apenas uma pessoa em frente à câmera.", None, None

        raw = faces[0]
        x, y, bw, bh = [int(v) for v in raw[:4]]
        fh, fw = frame_bgr.shape[:2]

        if bw < 95 or bh < 95:
            return False, "Aproxime-se um pouco mais da câmera.", None, (x, y, bw, bh)
        if x < 15 or y < 15 or (x + bw) > (fw - 15) or (y + bh) > (fh - 15):
            return False, "Centralize o rosto no quadro da câmera.", None, (x, y, bw, bh)

        pose_ok, pose_msg = self._is_frontal_pose(raw)
        if not pose_ok:
            return False, pose_msg, None, (x, y, bw, bh)

        face_roi = frame_bgr[max(0, y) : min(fh, y + bh), max(0, x) : min(fw, x + bw)]
        if face_roi.size > 0:
            gray_mean = float(cv2.cvtColor(face_roi, cv2.COLOR_BGR2GRAY).mean())
            if gray_mean < 42.0:
                return False, "Ambiente escuro. Melhore a iluminação frontal.", None, (x, y, bw, bh)

        ear, _, _ = self.compute_ear_and_contours(frame_bgr, (x, y, bw, bh))
        if ear < 0.20:
            return False, "Mantenha os olhos abertos durante a captura.", None, (x, y, bw, bh)

        emb = self.extract_embedding(frame_bgr, raw)
        return True, "Amostra facial válida!", emb, (x, y, bw, bh)

    def analyze_frame(
        self,
        frame_bgr: np.ndarray,
        enrolled_users: Sequence[EnrolledUser],
        threshold: float = 0.58,
        require_liveness: bool = True,
    ) -> FaceAnalysisResult:
        """Executa pipeline completo de autenticação facial + consenso temporal + prova de vida."""
        res = FaceAnalysisResult()
        faces = self.detect_faces(frame_bgr)

        if faces is None or len(faces) == 0:
            self.tracker.missed_frames += 1
            if self.tracker.missed_frames > 2:
                self.tracker.reset()
            res.status_message = "Posicione o rosto em frente à câmera"
            return res

        self.tracker.missed_frames = 0
        res.face_found = True
        res.num_faces = len(faces)

        raw = faces[0]
        x, y, bw, bh = [int(v) for v in raw[:4]]
        res.bbox = (x, y, bw, bh)
        res.raw_face = raw

        if bw < 85 or bh < 85:
            self.tracker.reset()
            res.status_message = "Aproxime-se da câmera para autenticar"
            return res

        # Verifica continuidade espacial estrita do rosto (impede troca de pessoa ou celular)
        cx, cy = x + bw * 0.5, y + bh * 0.5
        size = float(max(bw, bh))
        if self.tracker.last_center is not None:
            dist = float(
                np.hypot(cx - self.tracker.last_center[0], cy - self.tracker.last_center[1])
            )
            size_ratio = abs(size - self.tracker.last_size) / max(self.tracker.last_size, 1.0)
            if dist > size * 0.28 or size_ratio > 0.28:
                self.tracker.reset()

        self.tracker.last_center = (cx, cy)
        self.tracker.last_size = size

        ear, r_poly, l_poly = self.compute_ear_and_contours(frame_bgr, res.bbox)
        res.ear = ear
        res.right_eye_contour = r_poly
        res.left_eye_contour = l_poly

        pose_ok, pose_msg = self._is_frontal_pose(raw)
        if not pose_ok:
            self.tracker.reset_identity()
            res.status_message = pose_msg
            return res

        res.quality_ok = True

        # Extração e consenso biométrico 128D
        emb = self.extract_embedding(frame_bgr, raw)
        res.embedding = emb
        matched_user, consensus_score = self.match_user(emb, enrolled_users, threshold)
        res.best_score = consensus_score

        # Durante os poucos frames de olhos efetivamente fechados (piscada em curso),
        # só mantém o estado se o dono JÁ tiver sido validado em >= 4 frames consecutivos antes!
        close_thresh_est = (
            max(0.145, self.tracker.open_ear_baseline * 0.74)
            if self.tracker.open_ear_baseline > 0.0
            else 0.18
        )
        in_valid_blink_closure = (
            require_liveness
            and self.tracker.matched_user_id is not None
            and self.tracker.open_frames_count >= 4
            and ear < close_thresh_est
            and consensus_score >= (threshold - 0.06)
        )

        if matched_user is None and not in_valid_blink_closure:
            # Rosto NÃO autorizado — zera imediatamente qualquer progresso anterior!
            self.tracker.reset_identity()
            if self.tracker.unauthorized_since is None:
                self.tracker.unauthorized_since = time.monotonic()
            res.liveness_stage = "NAO_AUTORIZADO"
            res.status_message = "Acesso negado: rosto não autorizado neste computador"
            return res

        if matched_user is None and in_valid_blink_closure:
            # Mantém referência ao dono já confirmado apenas enquanto a pálpebra está fechada
            matched_user = next(
                (u for u in enrolled_users if u.id == self.tracker.matched_user_id), None
            )
            if matched_user is None:
                self.tracker.reset_identity()
                return res

        assert matched_user is not None
        res.matched_user = matched_user
        self.tracker.unauthorized_since = None

        if self.tracker.matched_user_id != matched_user.id:
            self.tracker.reset_identity()
            self.tracker.matched_user_id = matched_user.id
            self.tracker.track_started_at = time.monotonic()

        if ear >= close_thresh_est:
            self.tracker.score_history.append(consensus_score)

        rolling_mean = (
            float(sum(self.tracker.score_history) / len(self.tracker.score_history))
            if self.tracker.score_history
            else consensus_score
        )

        # Modo sem piscada: ainda assim exige >= 4 frames consecutivos acima do limiar!
        if not require_liveness:
            if len(self.tracker.score_history) >= 4 and rolling_mean >= threshold:
                res.liveness_passed = True
                res.liveness_stage = "CONFIRMADO"
                res.authenticated = True
                res.status_message = f"Bem-vindo(a), {matched_user.name}!"
            else:
                res.liveness_stage = "CONFIRMANDO_IDENTIDADE"
                res.status_message = f"Confirmando identidade de {matched_user.name}..."
            return res

        # Etapa 1: exige >= 4 frames consecutivos de olhos abertos com identidade estrita aprovada
        if self.tracker.open_frames_count < 4:
            if ear >= 0.21 and consensus_score >= threshold:
                if self.tracker.open_ear_baseline == 0.0:
                    self.tracker.open_ear_baseline = float(np.clip(ear, 0.22, 0.42))
                else:
                    self.tracker.open_ear_baseline = float(
                        np.clip(0.7 * self.tracker.open_ear_baseline + 0.3 * ear, 0.22, 0.42)
                    )
                self.tracker.open_frames_count += 1
            res.liveness_stage = "CALIBRANDO_OLHOS"
            res.status_message = f"Identificando {matched_user.name}... mantenha os olhos abertos"
            return res

        close_thresh = max(0.145, self.tracker.open_ear_baseline * 0.74)
        reopen_thresh = max(0.195, self.tracker.open_ear_baseline * 0.85)

        # Etapa 2: aguarda o fechamento rápido das pálpebras (piscada)
        if not self.tracker.saw_blink_dip:
            if ear < close_thresh:
                self.tracker.blink_closed_frames += 1
                self.tracker.saw_blink_dip = True
                self.tracker.reopen_verified_frames = 0
                res.liveness_stage = "PISCANDO"
                res.status_message = f"Detectando piscada de {matched_user.name}..."
            else:
                if ear > self.tracker.open_ear_baseline * 0.90:
                    self.tracker.open_ear_baseline = float(
                        np.clip(0.9 * self.tracker.open_ear_baseline + 0.1 * ear, 0.22, 0.42)
                    )
                res.liveness_stage = "AGUARDANDO_PISCADA"
                res.status_message = f"Olá, {matched_user.name}! Pisque os olhos para liberar"
            return res

        # Etapa 3: pálpebras fechadas -> aguarda reabertura natural
        if ear < close_thresh:
            self.tracker.blink_closed_frames += 1
            if self.tracker.blink_closed_frames > 16:
                self.tracker.blink_closed_frames = 0
                self.tracker.saw_blink_dip = False
                self.tracker.reopen_verified_frames = 0
            res.liveness_stage = "PISCANDO"
            res.status_message = "Abra os olhos normalmente para concluir..."
            return res

        # Etapa 4: revalidação estrita em múltiplos frames com olhos reabertos no mesmo rosto!
        if ear >= reopen_thresh:
            strict_user, strict_score = self.match_user(emb, enrolled_users, threshold)
            if (
                strict_user is not None
                and strict_user.id == matched_user.id
                and strict_score >= threshold
                and rolling_mean >= threshold
            ):
                self.tracker.reopen_verified_frames += 1
                if self.tracker.reopen_verified_frames >= 2:
                    self.tracker.liveness_confirmed = True
                    res.liveness_passed = True
                    res.liveness_stage = "CONFIRMADO"
                    res.authenticated = True
                    res.best_score = rolling_mean
                    res.status_message = f"Acesso liberado! Bem-vindo(a), {matched_user.name}."
                    return res
            else:
                self.tracker.reset_identity()
                res.liveness_stage = "NAO_AUTORIZADO"
                res.status_message = "Falha na confirmação pós-piscada. Tente novamente."
                return res

        res.liveness_stage = "REABRINDO_OLHOS"
        res.status_message = f"Confirmando {matched_user.name}..."
        return res


def draw_hud_overlay(
    frame_bgr: np.ndarray,
    analysis: FaceAnalysisResult,
    mirror: bool = True,
) -> np.ndarray:
    """Desenha HUD biométrico moderno sobre o frame para feedback visual claro."""
    vis = cv2.flip(frame_bgr, 1) if mirror else frame_bgr.copy()
    h, w = vis.shape[:2]

    if not analysis.face_found or analysis.bbox is None:
        cv2.ellipse(
            vis,
            (w // 2, h // 2),
            (int(w * 0.20), int(h * 0.32)),
            0,
            0,
            360,
            (110, 130, 150),
            2,
            cv2.LINE_AA,
        )
        return vis

    bx, by, bw, bh = analysis.bbox
    if mirror:
        bx = w - bx - bw

    if analysis.authenticated:
        color = (80, 220, 100)
    elif analysis.matched_user is not None:
        color = (255, 195, 40)
    elif not analysis.quality_ok:
        color = (180, 180, 195)
    else:
        color = (65, 70, 235)

    corner = max(16, min(bw, bh) // 5)
    thickness = 2
    x2, y2 = bx + bw, by + bh
    cv2.line(vis, (bx, by), (bx + corner, by), color, thickness, cv2.LINE_AA)
    cv2.line(vis, (bx, by), (bx, by + corner), color, thickness, cv2.LINE_AA)
    cv2.line(vis, (x2, by), (x2 - corner, by), color, thickness, cv2.LINE_AA)
    cv2.line(vis, (x2, by), (x2, by + corner), color, thickness, cv2.LINE_AA)
    cv2.line(vis, (bx, y2), (bx + corner, y2), color, thickness, cv2.LINE_AA)
    cv2.line(vis, (bx, y2), (bx, y2 - corner), color, thickness, cv2.LINE_AA)
    cv2.line(vis, (x2, y2), (x2 - corner, y2), color, thickness, cv2.LINE_AA)
    cv2.line(vis, (x2, y2), (x2, y2 - corner), color, thickness, cv2.LINE_AA)

    for eye_poly in (analysis.right_eye_contour, analysis.left_eye_contour):
        if eye_poly is not None:
            pts = eye_poly.copy()
            if mirror:
                pts[:, 0] = w - pts[:, 0]
            cv2.polylines(
                vis, [pts], isClosed=True, color=(235, 225, 60), thickness=1, lineType=cv2.LINE_AA
            )

    if analysis.matched_user is not None:
        tag = f"{analysis.matched_user.name} ({int(min(99, analysis.best_score * 100))}%)"
    elif not analysis.quality_ok:
        tag = "AJUSTE A POSICAO"
    else:
        tag = f"NAO AUTORIZADO ({int(min(99, analysis.best_score * 100))}%)"

    (tw, th), _ = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX, 0.52, 1)
    ty = max(th + 10, by - 8)
    cv2.rectangle(vis, (bx, ty - th - 8), (bx + tw + 14, ty + 4), color, -1)
    cv2.putText(
        vis,
        tag,
        (bx + 7, ty - 2),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.52,
        (15, 15, 20),
        1,
        cv2.LINE_AA,
    )
    return vis
