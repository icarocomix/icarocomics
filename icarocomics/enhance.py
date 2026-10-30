"""Auto-miglioramento delle tavole in stile Picasa "I'm Feeling Lucky".

Pipeline (solo Pillow, tutto in C, il GIL viene rilasciato durante le operazioni pesanti):
  1. statistiche su una copia ridotta (~512 px) per non pagare istogrammi a piena risoluzione;
  2. stretching dei livelli con punti nero/bianco ai percentili 0,5% / 99,5%, per canale ma
     "ancorato" alla luminanza: corregge la dominante (carta ingiallita) senza stravolgere
     tavole con ampie campiture di un solo colore;
  3. correzione gamma adattiva verso una luminanza media obiettivo, attenuata e limitata;
     i passi 2 e 3 sono fusi in un'unica LUT a 256 voci per canale (una sola passata);
  4. tavole quasi acromatiche (manga in RGB con carta ingiallita) -> scala di grigi pura,
     tavole a colori -> leggero aumento di saturazione;
  5. contrasto locale con unsharp mask a raggio ampio, proporzionale alla pagina.
"""

from __future__ import annotations

import math

from PIL import Image, ImageEnhance, ImageFilter

_LOW_PCT = 0.005
_HIGH_PCT = 0.995
_CHANNEL_WEIGHT = 0.7      # quanto i punti nero/bianco di canale si discostano da quelli di luminanza
_MIN_SPAN = 64             # limite al guadagno dello stretching (max ~4x) per non amplificare rumore
_TARGET_MEAN = 0.55
_GAMMA_STRENGTH = 0.35
_GAMMA_RANGE = (0.75, 1.30)
_GRAY_MEAN_SAT = 12        # sotto questa saturazione media la tavola è considerata in bianco e nero
_GRAY_P95_SAT = 40
_COLOR_BOOST = 1.15
_LOCAL_CONTRAST = 0.4      # equivale a UnsharpMask percent=40


def _percentile(hist: list[int], frac: float) -> int:
    total = sum(hist)
    if total == 0:
        return 0
    target = frac * total
    acc = 0
    for value, count in enumerate(hist):
        acc += count
        if acc >= target:
            return value
    return len(hist) - 1


def _span(lo: float, hi: float) -> tuple[float, float]:
    # Allargo simmetricamente gli intervalli troppo stretti: una pagina quasi uniforme non
    # deve essere "esplosa" fino al rumore di scansione.
    if hi - lo < _MIN_SPAN:
        mid = (lo + hi) / 2.0
        lo, hi = mid - _MIN_SPAN / 2.0, mid + _MIN_SPAN / 2.0
        if lo < 0:
            lo, hi = 0.0, float(_MIN_SPAN)
        if hi > 255:
            lo, hi = 255.0 - _MIN_SPAN, 255.0
    return lo, hi


def _gamma_for(lum_hist: list[int], lo: float, hi: float) -> float:
    total = sum(lum_hist) or 1
    scale = hi - lo
    mean = sum(c * min(1.0, max(0.0, (v - lo) / scale)) for v, c in enumerate(lum_hist)) / total
    mean = min(0.97, max(0.03, mean))
    # Esponente che porterebbe la media esattamente all'obiettivo, poi attenuato: voglio
    # schiarire le scansioni scure e dare corpo a quelle slavate, non uniformare tutto.
    full = math.log(_TARGET_MEAN) / math.log(mean)
    return min(_GAMMA_RANGE[1], max(_GAMMA_RANGE[0], full ** _GAMMA_STRENGTH))


def _lut(lo: float, hi: float, gamma: float) -> list[int]:
    scale = hi - lo
    out = []
    for v in range(256):
        x = min(1.0, max(0.0, (v - lo) / scale))
        out.append(int(round(255.0 * (x ** gamma))))
    return out


def _stats_image(img: Image.Image) -> Image.Image:
    factor = max(1, max(img.size) // 512)
    return img.reduce(factor) if factor > 1 else img


def _is_achromatic(small_rgb: Image.Image) -> bool:
    sat = small_rgb.convert("HSV").getchannel("S")
    hist = sat.histogram()
    total = sum(hist) or 1
    mean = sum(v * c for v, c in enumerate(hist)) / total
    return mean < _GRAY_MEAN_SAT and _percentile(hist, 0.95) < _GRAY_P95_SAT


def auto_enhance(img: Image.Image) -> Image.Image:
    """Restituisce una nuova immagine migliorata; accetta solo modi 'L' o 'RGB'."""
    if img.mode not in ("L", "RGB"):
        raise ValueError(f"modo immagine non supportato: {img.mode}")

    small = _stats_image(img)
    lum_hist = (small if small.mode == "L" else small.convert("L")).histogram()
    lum_lo, lum_hi = _span(_percentile(lum_hist, _LOW_PCT), _percentile(lum_hist, _HIGH_PCT))
    gamma = _gamma_for(lum_hist, lum_lo, lum_hi)

    if img.mode == "RGB" and _is_achromatic(small):
        # Manga/b&n scansionati a colori: la dominante della carta sparisce del tutto passando
        # al grigio, e la pagina occupa un terzo della memoria.
        img = img.convert("L")

    if img.mode == "L":
        out = img.point(_lut(lum_lo, lum_hi, gamma))
    else:
        hist = small.histogram()
        luts: list[int] = []
        for ch in range(3):
            ch_hist = hist[ch * 256:(ch + 1) * 256]
            lo = lum_lo + _CHANNEL_WEIGHT * (_percentile(ch_hist, _LOW_PCT) - lum_lo)
            hi = lum_hi + _CHANNEL_WEIGHT * (_percentile(ch_hist, _HIGH_PCT) - lum_hi)
            luts.extend(_lut(*_span(lo, hi), gamma))
        out = img.point(luts)
        out = ImageEnhance.Color(out).enhance(_COLOR_BOOST)

    return _local_contrast(out)


def _local_contrast(img: Image.Image) -> Image.Image:
    # Unsharp mask a raggio ampio: la sfocatura è un segnale a bassa frequenza, quindi la
    # calcolo su una copia ridotta 4x e la riporto a piena risoluzione (misurato: scarto
    # medio < 0,4 livelli rispetto all'UnsharpMask completo, ~40% di tempo in meno).
    # Image.blend con alpha > 1 estrapola: img + (alpha - 1) * (img - blur).
    radius = max(2.0, max(img.size) / 200.0)
    factor = 4 if min(img.size) >= 256 else 1
    small = img.reduce(factor) if factor > 1 else img
    blur = small.filter(ImageFilter.GaussianBlur(radius / factor))
    if factor > 1:
        blur = blur.resize(img.size, Image.Resampling.BILINEAR)
    return Image.blend(blur, img, 1.0 + _LOCAL_CONTRAST)
