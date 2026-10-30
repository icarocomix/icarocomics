"""Genera archivi CBZ di prova, incluso uno di grandi dimensioni (~1 GB) per misurare
che l'apertura legga solo la Central Directory.

Uso: python3 tests/make_samples.py <cartella_destinazione> [numero_pagine_grande]
"""

from __future__ import annotations

import io
import os
import sys
import zipfile

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def _font(size: int) -> ImageFont.ImageFont:
    for name in ("DejaVuSans-Bold.ttf", "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def _page(base: np.ndarray, number: int, total: int, washed: bool) -> bytes:
    img = Image.fromarray(base, "RGB")
    draw = ImageDraw.Draw(img)
    w, h = img.size
    # Vignette e numero di pagina ben visibili, per riconoscere le pagine negli screenshot.
    for k in range(3):
        y0 = 120 + k * (h - 240) // 3
        draw.rectangle([100, y0, w - 100, y0 + (h - 240) // 3 - 60], outline=(10, 10, 10), width=14)
    font = _font(w // 5)
    label = f"{number}/{total}"
    tw = draw.textlength(label, font=font)
    draw.text(((w - tw) / 2, h / 2 - w / 8), label, fill=(20, 20, 20), font=font, stroke_width=8,
              stroke_fill=(250, 250, 250))
    if washed:
        # Simulo una scansione "slavata": contrasto compresso e dominante gialla.
        arr = np.asarray(img, dtype=np.float32)
        arr = 70 + arr * 0.55
        arr[..., 2] *= 0.85
        img = Image.fromarray(arr.clip(0, 255).astype(np.uint8), "RGB")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def _bases(rng: np.random.Generator, n: int, size: tuple[int, int]) -> list[np.ndarray]:
    w, h = size
    out = []
    for i in range(n):
        # Rumore + gradiente: comprime male in JPEG, quindi pagine "pesanti" come vere scansioni.
        grad = np.linspace(180, 245, h, dtype=np.float32)[:, None, None]
        tint = np.array([1.0, 0.97 - 0.02 * i, 0.9 + 0.02 * i], dtype=np.float32)
        noise = rng.normal(0, 38, (h, w, 3)).astype(np.float32)
        out.append((grad * tint + noise).clip(0, 255).astype(np.uint8))
    return out


def make_cbz(path: str, pages: int, size: tuple[int, int], washed_every: int = 0) -> None:
    rng = np.random.default_rng(42)
    bases = _bases(rng, 4, size)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
        for i in range(pages):
            washed = bool(washed_every) and i % washed_every == 0
            # Nomi volutamente "difficili" per l'ordinamento naturale (2 prima di 10).
            zf.writestr(f"Tavole/pagina {i + 1}.jpg", _page(bases[i % len(bases)], i + 1, pages, washed))
        zf.writestr("ComicInfo.xml", "<ComicInfo><Title>Test IcaroComics</Title></ComicInfo>")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    out = sys.argv[1]
    big_pages = int(sys.argv[2]) if len(sys.argv) > 2 else 110
    os.makedirs(out, exist_ok=True)
    small = os.path.join(out, "test_piccolo.cbz")
    make_cbz(small, 12, (1200, 1800), washed_every=2)
    print(small, os.path.getsize(small) // 1048576, "MB")
    big = os.path.join(out, "test_grande.cbz")
    make_cbz(big, big_pages, (2600, 3900), washed_every=5)
    print(big, os.path.getsize(big) // 1048576, "MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
