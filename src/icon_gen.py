"""Geração programática do ícone corporativo do SentinelPC (escudo + scanner biométrico)."""

from __future__ import annotations

from pathlib import Path
from PIL import Image, ImageDraw


def create_shield_icon(size: int = 256) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    pad = int(size * 0.08)
    w = size - 2 * pad
    cx = size // 2

    # Polígono de escudo moderno
    shield_pts = [
        (cx, pad),
        (size - pad, int(size * 0.22)),
        (int(size * 0.88), int(size * 0.60)),
        (cx, size - pad),
        (int(size * 0.12), int(size * 0.60)),
        (pad, int(size * 0.22)),
    ]
    draw.polygon(shield_pts, fill=(15, 23, 42, 255), outline=(56, 189, 248, 255))

    # Borda interna ciano
    inner_pts = [
        (cx, pad + int(w * 0.06)),
        (size - pad - int(w * 0.06), int(size * 0.25)),
        (int(size * 0.83), int(size * 0.58)),
        (cx, size - pad - int(w * 0.08)),
        (int(size * 0.17), int(size * 0.58)),
        (pad + int(w * 0.06), int(size * 0.25)),
    ]
    draw.line(inner_pts + [inner_pts[0]], fill=(14, 165, 233, 220), width=max(2, size // 36))

    # Retículo biométrico central (olho/rosto)
    ry = int(size * 0.46)
    r_outer = int(size * 0.18)
    r_inner = int(size * 0.07)
    draw.ellipse(
        (cx - r_outer, ry - r_outer, cx + r_outer, ry + r_outer),
        outline=(56, 189, 248, 255),
        width=max(2, size // 32),
    )
    draw.ellipse(
        (cx - r_inner, ry - r_inner, cx + r_inner, ry + r_inner),
        fill=(34, 211, 238, 255),
    )

    # Cantoneiras de leitura facial
    b = int(size * 0.24)
    c = int(size * 0.08)
    lw = max(2, size // 36)
    color = (74, 222, 128, 255)
    # Sup-Esq
    draw.line([(cx - b, ry - b), (cx - b + c, ry - b)], fill=color, width=lw)
    draw.line([(cx - b, ry - b), (cx - b, ry - b + c)], fill=color, width=lw)
    # Sup-Dir
    draw.line([(cx + b, ry - b), (cx + b - c, ry - b)], fill=color, width=lw)
    draw.line([(cx + b, ry - b), (cx + b, ry - b + c)], fill=color, width=lw)
    # Inf-Esq
    draw.line([(cx - b, ry + b), (cx - b + c, ry + b)], fill=color, width=lw)
    draw.line([(cx - b, ry + b), (cx - b, ry + b - c)], fill=color, width=lw)
    # Inf-Dir
    draw.line([(cx + b, ry + b), (cx + b - c, ry + b)], fill=color, width=lw)
    draw.line([(cx + b, ry + b), (cx + b, ry + b - c)], fill=color, width=lw)

    return img


def ensure_icon_files(ico_path: Path) -> None:
    ico_path.parent.mkdir(parents=True, exist_ok=True)
    base = create_shield_icon(256)
    base.save(
        str(ico_path),
        format="ICO",
        sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)],
    )
