"""Caricamento asincrono delle pagine: lettura on-demand dall'archivio, decodifica,
auto-miglioramento opzionale, pre-ridimensionamento per la vista e prefetch delle vicine."""

from __future__ import annotations

import io
import time
from dataclasses import dataclass, field

from PIL import Image, ImageOps
from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal
from PyQt6.QtGui import QImage

from icarocomics.archive import ComicArchive
from icarocomics.cache import LruCache
from icarocomics.enhance import auto_enhance
from icarocomics.layout import ViewTarget, ZoomMode, compute_scale

# Le scansioni di fumetti possono superare la soglia anti "decompression bomb" di Pillow
# (89 Mpx); alzo il limite mantenendo comunque un tetto.
Image.MAX_IMAGE_PIXELS = 400_000_000


@dataclass
class PageImage:
    index: int
    enhanced: bool
    image: QImage                    # piena risoluzione (Grayscale8 o RGB888)
    scaled: QImage | None = None     # versione ridotta pronta per la vista (RGB32)
    scaled_for: tuple | None = None  # (larghezza, altezza) fisiche per cui è stata prodotta
    decode_ms: float = 0.0
    extra: dict = field(default_factory=dict)

    @property
    def width(self) -> int:
        return self.image.width()

    @property
    def height(self) -> int:
        return self.image.height()

    @property
    def cost(self) -> int:
        cost = self.image.sizeInBytes()
        if self.scaled is not None:
            cost += self.scaled.sizeInBytes()
        return cost


def decode_image(data: bytes) -> Image.Image:
    """Decodifica e normalizza in 'L' (scala di grigi) o 'RGB': il grigio occupa 1/3 della RAM."""
    img = Image.open(io.BytesIO(data))
    img.load()
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:  # EXIF malformato: non è un motivo per non mostrare la pagina
        pass
    mode = img.mode
    if mode == "L":
        return img
    if mode in ("1", "I", "I;16", "I;16B", "I;16L", "F"):
        return img.convert("L")
    if mode in ("LA", "La") or (mode == "P" and "transparency" in img.info) or mode in ("RGBA", "RGBa", "PA"):
        # Le trasparenze vanno composte su carta bianca, non su nero.
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.getchannel("A"))
        return bg
    return img.convert("RGB") if mode != "RGB" else img


def pil_to_qimage(img: Image.Image) -> QImage:
    w, h = img.size
    if img.mode == "L":
        fmt, bpl = QImage.Format.Format_Grayscale8, w
    elif img.mode == "RGB":
        fmt, bpl = QImage.Format.Format_RGB888, w * 3
    else:
        raise ValueError(f"modo non supportato: {img.mode}")
    buf = img.tobytes()
    # copy() stacca il QImage dal buffer Python, che altrimenti verrebbe liberato.
    return QImage(buf, w, h, bpl, fmt).copy()


def scaled_physical_size(w: int, h: int, target: ViewTarget) -> tuple[int, int, float]:
    scale = compute_scale(w, h, target)
    phys = scale * target.dpr
    return max(1, round(w * phys)), max(1, round(h * phys)), phys


def make_scaled(img: Image.Image, target: ViewTarget) -> tuple[QImage | None, tuple | None]:
    """Riduzione di qualità (Lanczos) eseguita nel worker; None se serve ingrandire."""
    tw, th, phys = scaled_physical_size(img.width, img.height, target)
    if phys >= 0.999:
        return None, None
    small = img.resize((tw, th), Image.Resampling.LANCZOS, reducing_gap=2.0)
    q = pil_to_qimage(small).convertToFormat(QImage.Format.Format_RGB32)
    q.setDevicePixelRatio(target.dpr)
    return q, (tw, th)


class _Signals(QObject):
    done = pyqtSignal(int, object)          # generazione, PageImage
    failed = pyqtSignal(int, int, bool, str)  # generazione, indice, migliorata, messaggio


class _PageTask(QRunnable):
    def __init__(self, loader: PageLoader, generation: int, archive: ComicArchive,
                 index: int, enhanced: bool, target: ViewTarget):
        super().__init__()
        self.setAutoDelete(True)
        self._loader = loader
        self._gen = generation
        self._archive = archive
        self.index = index
        self.enhanced = enhanced
        self._target = target
        self.started = False

    def run(self) -> None:
        self.started = True
        loader = self._loader
        if loader.generation != self._gen:
            return  # nel frattempo è stato aperto un altro fumetto
        t0 = time.perf_counter()
        try:
            key = (self._gen, self.index)
            data = loader.bytes_cache.get(key)
            if data is None:
                data = self._archive.read_page(self.index)
                loader.bytes_cache.put(key, data, len(data))
            t_read = time.perf_counter()
            img = decode_image(data)
            if self.enhanced:
                img = auto_enhance(img)
            full = pil_to_qimage(img)
            scaled, scaled_for = make_scaled(img, self._target)
            t_end = time.perf_counter()
            page = PageImage(
                index=self.index, enhanced=self.enhanced, image=full, scaled=scaled,
                scaled_for=scaled_for, decode_ms=(t_end - t0) * 1000.0,
                extra={"read_ms": (t_read - t0) * 1000.0, "bytes": len(data)},
            )
            loader.signals.done.emit(self._gen, page)
        except Exception as exc:  # qualunque errore della singola pagina va mostrato, non perso
            loader.signals.failed.emit(self._gen, self.index, self.enhanced, f"{type(exc).__name__}: {exc}")


class PageLoader(QObject):
    page_ready = pyqtSignal(object)            # PageImage
    page_failed = pyqtSignal(int, bool, str)   # indice, migliorata, messaggio

    PREFETCH_AHEAD = 2
    PREFETCH_BEHIND = 1

    def __init__(self, decoded_budget_mb: int = 640, bytes_budget_mb: int = 192,
                 threads: int = 2, parent: QObject | None = None):
        super().__init__(parent)
        self.generation = 0
        self.archive: ComicArchive | None = None
        self.decoded = LruCache(decoded_budget_mb * 1024 * 1024)
        self.bytes_cache = LruCache(bytes_budget_mb * 1024 * 1024)
        self.signals = _Signals()
        self.signals.done.connect(self._on_done)
        self.signals.failed.connect(self._on_failed)
        self._pool = QThreadPool(self)
        # Poche thread di proposito: la macchina ha risorse limitate e la decodifica JPEG
        # di una pagina richiede comunque poche decine di ms.
        self._pool.setMaxThreadCount(max(1, threads))
        self._inflight: dict[tuple[int, bool], _PageTask] = {}
        self.target = ViewTarget(ZoomMode.FIT_PAGE, 1.0, 0, 0, 1.0)

    # --- ciclo di vita del documento -------------------------------------------------
    def set_archive(self, archive: ComicArchive | None) -> None:
        self.generation += 1
        self._pool.clear()
        self._inflight.clear()
        self.decoded.clear()
        self.bytes_cache.clear()
        old, self.archive = self.archive, archive
        if old is not None:
            old.close()

    def shutdown(self) -> None:
        self.generation += 1
        self._pool.clear()
        self._pool.waitForDone(3000)
        if self.archive is not None:
            self.archive.close()
            self.archive = None

    def set_target(self, target: ViewTarget) -> None:
        self.target = target

    # --- richieste -------------------------------------------------------------------
    def get(self, index: int, enhanced: bool) -> PageImage | None:
        return self.decoded.get((index, enhanced))

    def is_pending(self, index: int, enhanced: bool) -> bool:
        return (index, enhanced) in self._inflight

    def request(self, index: int, enhanced: bool, priority: int) -> None:
        archive = self.archive
        if archive is None or not 0 <= index < len(archive):
            return
        key = (index, enhanced)
        if key in self.decoded or key in self._inflight:
            return
        task = _PageTask(self, self.generation, archive, index, enhanced, self.target)
        self._inflight[key] = task
        self._pool.start(task, priority)

    def schedule(self, current: int, enhanced: bool, step: int) -> None:
        """Pagina corrente con priorità massima, poi prefetch nella direzione di lettura."""
        if self.archive is None:
            return
        # Tolgo dalla coda i prefetch non ancora partiti: se l'utente sfoglia velocemente
        # non voglio decodificare pagine ormai lontane prima di quella che sta guardando.
        self._pool.clear()
        for key in [k for k, t in self._inflight.items() if not t.started]:
            del self._inflight[key]

        step = 1 if step >= 0 else -1
        wanted = [(current, 100)]
        for k in range(1, self.PREFETCH_AHEAD + 1):
            wanted.append((current + step * k, 50 - k))
        for k in range(1, self.PREFETCH_BEHIND + 1):
            wanted.append((current - step * k, 10 - k))
        n = len(self.archive)
        valid = [(i, p) for i, p in wanted if 0 <= i < n]
        self.decoded.set_protected([(i, enhanced) for i, _ in valid])
        self.bytes_cache.set_protected([(self.generation, i) for i, _ in valid])
        for index, prio in valid:
            self.request(index, enhanced, prio)

    def stats(self) -> tuple[int, int]:
        return len(self.decoded), self.decoded.size_bytes

    # --- risultati (thread GUI) -------------------------------------------------------
    def _on_done(self, generation: int, page: PageImage) -> None:
        if generation != self.generation:
            return
        self._inflight.pop((page.index, page.enhanced), None)
        self.decoded.put((page.index, page.enhanced), page, page.cost)
        self.page_ready.emit(page)

    def _on_failed(self, generation: int, index: int, enhanced: bool, message: str) -> None:
        if generation != self.generation:
            return
        self._inflight.pop((index, enhanced), None)
        self.page_failed.emit(index, enhanced, message)
