"""Camada de persistência SQLite para credenciais da TI, biometria facial e auditoria de intrusos."""

from __future__ import annotations

import getpass
import hashlib
import hmac
import os
import secrets
import socket
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

from src.paths import get_data_dir, get_db_path, get_intruders_dir

if TYPE_CHECKING:
    import numpy as np

PBKDF2_ITERATIONS = 200_000

DEFAULT_SETTINGS: dict[str, str] = {
    "lock_on_startup": "1",
    "lock_on_unlock": "1",
    "require_liveness": "1",
    "similarity_threshold": "0.58",
    "camera_index": "0",
    "intruder_cooldown_sec": "8",
}


@dataclass
class EnrolledUser:
    id: int
    name: str
    department: str
    active: bool
    created_at: str
    embeddings: list[np.ndarray]
    thumbnail_path: str | None = None


@dataclass
class AuditRecord:
    id: int
    timestamp: str
    event_type: str
    person_label: str
    similarity_score: float
    photo_path: str | None
    computer_name: str
    windows_user: str
    details: str


class StorageManager:
    """Gerencia o banco SQLite local com thread-safety e armazenamento de evidências."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or get_db_path()
        self._lock = threading.RLock()
        self._init_db()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS admin_credentials (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    salt_hex TEXT NOT NULL,
                    password_hash_hex TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    department TEXT NOT NULL DEFAULT '',
                    active INTEGER NOT NULL DEFAULT 1,
                    thumbnail_path TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS face_embeddings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    embedding BLOB NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    person_label TEXT NOT NULL DEFAULT '',
                    similarity_score REAL NOT NULL DEFAULT 0.0,
                    photo_path TEXT,
                    computer_name TEXT NOT NULL,
                    windows_user TEXT NOT NULL,
                    details TEXT NOT NULL DEFAULT ''
                );
                """
            )
            for key, default_val in DEFAULT_SETTINGS.items():
                conn.execute(
                    "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                    (key, default_val),
                )
            # Migração automática de segurança v2: eleva limiares antigos (< 0.50, ex: 0.38) para 0.58
            mig = conn.execute(
                "SELECT value FROM settings WHERE key = 'security_threshold_v2_migrated'"
            ).fetchone()
            if mig is None:
                row_thr = conn.execute(
                    "SELECT value FROM settings WHERE key = 'similarity_threshold'"
                ).fetchone()
                try:
                    curr_thr = float(row_thr["value"]) if row_thr else 0.38
                except ValueError:
                    curr_thr = 0.38
                if curr_thr < 0.50:
                    conn.execute(
                        "INSERT OR REPLACE INTO settings (key, value) VALUES ('similarity_threshold', '0.58')"
                    )
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES ('security_threshold_v2_migrated', '1')"
                )
            conn.commit()

    # ------------------------------------------------------------------
    # Senha Mestre da TI (PBKDF2-HMAC-SHA256)
    # ------------------------------------------------------------------
    def is_admin_configured(self) -> bool:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT 1 FROM admin_credentials WHERE id = 1").fetchone()
            return row is not None

    def set_admin_password(self, password: str) -> None:
        if len(password.strip()) < 4:
            raise ValueError("A senha da TI deve ter pelo menos 4 caracteres.")
        salt = secrets.token_bytes(16)
        pwd_hash = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS
        )
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO admin_credentials (id, salt_hex, password_hash_hex, updated_at)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    salt_hex = excluded.salt_hex,
                    password_hash_hex = excluded.password_hash_hex,
                    updated_at = excluded.updated_at
                """,
                (salt.hex(), pwd_hash.hex(), now_str),
            )
            conn.commit()

    def verify_admin_password(self, password: str) -> bool:
        if not password:
            return False
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT salt_hex, password_hash_hex FROM admin_credentials WHERE id = 1"
            ).fetchone()
            if row is None:
                return False
            salt = bytes.fromhex(row["salt_hex"])
            expected_hash = bytes.fromhex(row["password_hash_hex"])
            candidate = hashlib.pbkdf2_hmac(
                "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS
            )
            return hmac.compare_digest(candidate, expected_hash)

    # ------------------------------------------------------------------
    # Configurações
    # ------------------------------------------------------------------
    def get_setting(self, key: str, default: str = "") -> str:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                return DEFAULT_SETTINGS.get(key, default)
            return str(row["value"])

    def get_bool_setting(self, key: str, default: bool = True) -> bool:
        val = self.get_setting(key, "1" if default else "0")
        return val in ("1", "true", "True", "yes")

    def get_float_setting(self, key: str, default: float = 0.58) -> float:
        try:
            return float(self.get_setting(key, str(default)))
        except ValueError:
            return default

    def get_int_setting(self, key: str, default: int = 0) -> int:
        try:
            return int(self.get_setting(key, str(default)))
        except ValueError:
            return default

    def set_setting(self, key: str, value: Any) -> None:
        if isinstance(value, bool):
            str_val = "1" if value else "0"
        else:
            str_val = str(value)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, str_val),
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Usuários Autorizados & Embeddings Faciais
    # ------------------------------------------------------------------
    def enroll_user(
        self,
        name: str,
        department: str,
        embeddings: list[np.ndarray],
        thumbnail_bgr: np.ndarray | None = None,
    ) -> int:
        import cv2
        import numpy as np

        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Informe o nome do colaborador.")
        if not embeddings:
            raise ValueError("Nenhuma amostra facial capturada.")

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        thumb_path_str: str | None = None

        if thumbnail_bgr is not None:
            thumbs_dir = get_data_dir() / "perfis"
            thumbs_dir.mkdir(parents=True, exist_ok=True)
            safe_slug = "".join(c if c.isalnum() else "_" for c in clean_name)[:24]
            thumb_file = thumbs_dir / f"user_{safe_slug}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
            ok, buf = cv2.imencode(".jpg", thumbnail_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
            if ok:
                thumb_file.write_bytes(buf.tobytes())
                thumb_path_str = str(thumb_file)

        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO users (name, department, active, thumbnail_path, created_at)
                VALUES (?, ?, 1, ?, ?)
                """,
                (clean_name, department.strip(), thumb_path_str, now_str),
            )
            user_id = int(cur.lastrowid)
            for emb in embeddings:
                arr = np.asarray(emb, dtype=np.float32).reshape(1, -1)
                conn.execute(
                    """
                    INSERT INTO face_embeddings (user_id, embedding, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (user_id, arr.tobytes(), now_str),
                )
            conn.commit()
            return user_id

    def list_users(self, only_active: bool = False) -> list[EnrolledUser]:
        import numpy as np

        with self._lock, self._connect() as conn:
            query = "SELECT * FROM users"
            if only_active:
                query += " WHERE active = 1"
            query += " ORDER BY id ASC"
            user_rows = conn.execute(query).fetchall()
            result: list[EnrolledUser] = []
            for u in user_rows:
                emb_rows = conn.execute(
                    "SELECT embedding FROM face_embeddings WHERE user_id = ?",
                    (u["id"],),
                ).fetchall()
                embeddings = [
                    np.frombuffer(r["embedding"], dtype=np.float32).reshape(1, -1).copy()
                    for r in emb_rows
                ]
                result.append(
                    EnrolledUser(
                        id=int(u["id"]),
                        name=str(u["name"]),
                        department=str(u["department"]),
                        active=bool(u["active"]),
                        created_at=str(u["created_at"]),
                        embeddings=embeddings,
                        thumbnail_path=u["thumbnail_path"],
                    )
                )
            return result

    def has_enrolled_users(self) -> bool:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM face_embeddings fe
                JOIN users u ON u.id = fe.user_id
                WHERE u.active = 1
                LIMIT 1
                """
            ).fetchone()
            return row is not None

    def delete_user(self, user_id: int) -> None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT thumbnail_path FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            conn.commit()
            if row and row["thumbnail_path"]:
                try:
                    Path(row["thumbnail_path"]).unlink(missing_ok=True)
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # Auditoria & Fotos de Intrusos
    # ------------------------------------------------------------------
    def log_event(
        self,
        event_type: str,
        person_label: str = "",
        similarity_score: float = 0.0,
        frame_bgr: np.ndarray | None = None,
        details: str = "",
    ) -> int:
        now = datetime.now()
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")
        computer_name = socket.gethostname()
        try:
            windows_user = getpass.getuser()
        except Exception:
            windows_user = os.environ.get("USERNAME", "desconhecido")

        photo_path_str: str | None = None
        if frame_bgr is not None and getattr(frame_bgr, "size", 0) > 0:
            photo_path_str = self._save_evidence_photo(
                frame_bgr=frame_bgr,
                now=now,
                event_type=event_type,
                computer_name=computer_name,
                windows_user=windows_user,
                similarity_score=similarity_score,
            )

        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO audit_logs (
                    timestamp, event_type, person_label, similarity_score,
                    photo_path, computer_name, windows_user, details
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now_str,
                    event_type,
                    person_label,
                    float(similarity_score),
                    photo_path_str,
                    computer_name,
                    windows_user,
                    details,
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def _save_evidence_photo(
        self,
        frame_bgr: np.ndarray,
        now: datetime,
        event_type: str,
        computer_name: str,
        windows_user: str,
        similarity_score: float,
    ) -> str | None:
        """Salva a foto do intruso com barra de metadados forenses de data/hora/PC."""
        try:
            import cv2
            import numpy as np

            evidence = frame_bgr.copy()
            h, w = evidence.shape[:2]
            banner_h = 44
            canvas = np.zeros((h + banner_h, w, 3), dtype=np.uint8)
            canvas[:h, :w] = evidence
            canvas[h:, :] = (24, 24, 32)

            stamp = now.strftime("%d/%m/%Y %H:%M:%S")
            caption = (
                f"SENTINEL-PC | {stamp} | {event_type} | "
                f"PC: {computer_name} ({windows_user}) | Score: {similarity_score:.2f}"
            )
            cv2.putText(
                canvas,
                caption,
                (12, h + 28),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.48,
                (235, 235, 245),
                1,
                cv2.LINE_AA,
            )

            filename = f"evidencia_{now.strftime('%Y%m%d_%H%M%S_%f')[:19]}_{event_type.lower()}.jpg"
            target = get_intruders_dir() / filename
            # Usa imencode + write_bytes para suportar acentos no caminho do Windows (ex: Segurança_PC)
            ok, buf = cv2.imencode(".jpg", canvas, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
            if ok:
                target.write_bytes(buf.tobytes())
                return str(target)
        except Exception:
            pass
        return None

    def list_audit_logs(
        self, limit: int = 100, only_alerts: bool = False
    ) -> list[AuditRecord]:
        with self._lock, self._connect() as conn:
            query = "SELECT * FROM audit_logs"
            if only_alerts:
                query += (
                    " WHERE event_type IN "
                    "('INTRUSO_DETECTADO', 'FALHA_PROVA_VIDA', 'SENHA_TI_INVALIDA')"
                )
            query += " ORDER BY id DESC LIMIT ?"
            rows = conn.execute(query, (limit,)).fetchall()
            return [
                AuditRecord(
                    id=int(r["id"]),
                    timestamp=str(r["timestamp"]),
                    event_type=str(r["event_type"]),
                    person_label=str(r["person_label"]),
                    similarity_score=float(r["similarity_score"]),
                    photo_path=r["photo_path"],
                    computer_name=str(r["computer_name"]),
                    windows_user=str(r["windows_user"]),
                    details=str(r["details"]),
                )
                for r in rows
            ]

    def clear_audit_logs(self, delete_photos: bool = True) -> None:
        with self._lock, self._connect() as conn:
            if delete_photos:
                rows = conn.execute(
                    "SELECT photo_path FROM audit_logs WHERE photo_path IS NOT NULL"
                ).fetchall()
                for r in rows:
                    try:
                        Path(r["photo_path"]).unlink(missing_ok=True)
                    except OSError:
                        pass
            conn.execute("DELETE FROM audit_logs")
            conn.commit()
