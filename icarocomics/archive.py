"""Accesso casuale agli archivi di fumetti (CBZ/CBR/CB7) senza estrazione su disco.

Ogni backend legge all'apertura SOLO l'indice delle voci e, su richiesta, restituisce
in RAM i byte compressi di una singola pagina. Nessun file viene mai scritto in /tmp.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
import zipfile
from abc import ABC, abstractmethod
from dataclasses import dataclass

IMAGE_EXTENSIONS = frozenset(
    {".jpg", ".jpeg", ".jpe", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".avif", ".jxl"}
)

_ZIP_MAGICS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_RAR4_MAGIC = b"Rar!\x1a\x07\x00"
_RAR5_MAGIC = b"Rar!\x1a\x07\x01\x00"
_7Z_MAGIC = b"7z\xbc\xaf\x27\x1c"

# Timeout generoso per l'estrazione di una pagina: in un RAR "solido" la pagina N richiede di
# decomprimere tutte le precedenti, quindi su archivi da 1 GB può servire qualche secondo.
_EXTRACT_TIMEOUT_S = 120
_LIST_TIMEOUT_S = 30


class ArchiveError(Exception):
    """Errore di apertura o lettura di un archivio."""


@dataclass(frozen=True)
class PageEntry:
    name: str
    size: int  # dimensione non compressa dichiarata nell'indice (0 se ignota)


_NATURAL_SPLIT = re.compile(r"(\d+)")


def natural_key(name: str) -> tuple:
    # Ordino "pag2" prima di "pag10" come farebbe un essere umano: le sequenze numeriche
    # diventano interi, il resto viene confrontato senza distinzione di maiuscole.
    parts = _NATURAL_SPLIT.split(name.replace("\\", "/").casefold())
    return tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in parts)


def is_page_name(name: str) -> bool:
    norm = name.replace("\\", "/")
    base = norm.rsplit("/", 1)[-1]
    if not base or base.startswith("."):
        return False
    # Scarto i metadati di macOS che molti CBZ si portano dietro.
    if norm.startswith("__MACOSX/") or "/__MACOSX/" in norm:
        return False
    return os.path.splitext(base)[1].lower() in IMAGE_EXTENSIONS


def sort_pages(entries: list[PageEntry]) -> list[PageEntry]:
    return sorted(entries, key=lambda e: natural_key(e.name))


class ComicArchive(ABC):
    """Interfaccia comune: indice delle pagine + lettura on-demand di una singola pagina."""

    kind = "?"

    def __init__(self, path: str):
        self.path = path
        self.pages: list[PageEntry] = []
        self.index_time_ms = 0.0
        self.solid = False

    def __len__(self) -> int:
        return len(self.pages)

    @abstractmethod
    def read_page(self, index: int) -> bytes:
        """Restituisce i byte (ancora codificati JPEG/PNG/...) della pagina richiesta."""

    def close(self) -> None:
        pass

    def _entry(self, index: int) -> PageEntry:
        if not 0 <= index < len(self.pages):
            raise IndexError(f"pagina {index} fuori intervallo (0..{len(self.pages) - 1})")
        return self.pages[index]


class ZipComicArchive(ComicArchive):
    kind = "ZIP"

    def __init__(self, path: str):
        super().__init__(path)
        try:
            # ZipFile legge esclusivamente l'End Of Central Directory e la Central Directory
            # (in coda al file): costo proporzionale al numero di voci, non alla dimensione.
            self._zip = zipfile.ZipFile(path, "r")
        except (zipfile.BadZipFile, OSError) as exc:
            raise ArchiveError(f"Archivio ZIP non valido: {exc}") from exc
        self._infos: dict[str, zipfile.ZipInfo] = {}
        entries = []
        for info in self._zip.infolist():
            if info.is_dir() or not is_page_name(info.filename):
                continue
            self._infos[info.filename] = info
            entries.append(PageEntry(info.filename, info.file_size))
        self.pages = sort_pages(entries)
        # Serializzo le letture: un solo file handle condiviso tra i thread di prefetch.
        # La sezione critica copre solo la lettura/inflate, la decodifica avviene fuori.
        self._lock = threading.Lock()
        self._closed = False

    def read_page(self, index: int) -> bytes:
        entry = self._entry(index)
        with self._lock:
            if self._closed:
                raise ArchiveError("archivio chiuso")
            try:
                return self._zip.read(self._infos[entry.name])
            except RuntimeError as exc:  # voce cifrata
                raise ArchiveError(f"Pagina protetta da password: {entry.name}") from exc
            except (zipfile.BadZipFile, OSError, EOFError) as exc:
                raise ArchiveError(f"Pagina corrotta {entry.name}: {exc}") from exc

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                self._zip.close()


def _run(cmd: list[str], timeout: float) -> bytes:
    try:
        proc = subprocess.run(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ArchiveError(f"Timeout eseguendo {cmd[0]}") from exc
    except OSError as exc:
        raise ArchiveError(f"Impossibile eseguire {cmd[0]}: {exc}") from exc
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise ArchiveError(f"{cmd[0]} ha restituito {proc.returncode}: {err[-1] if err else ''}")
    return proc.stdout


def _decode_names(raw: bytes) -> list[str]:
    # surrogateescape garantisce il round-trip esatto dei byte del nome verso la riga di
    # comando anche se l'archivio contiene nomi non UTF-8.
    return [ln for ln in raw.decode("utf-8", "surrogateescape").splitlines() if ln]


def _read_vint(buf: bytes, pos: int) -> tuple[int, int]:
    value, shift = 0, 0
    while pos < len(buf):
        b = buf[pos]
        pos += 1
        value |= (b & 0x7F) << shift
        if not b & 0x80:
            return value, pos
        shift += 7
    raise ValueError("vint troncato")


def detect_rar_solid(path: str) -> bool:
    """Legge il flag 'solid' dal main header RAR4/RAR5 (best effort, solo pochi byte)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(64)
        if head.startswith(_RAR5_MAGIC):
            pos = len(_RAR5_MAGIC) + 4  # salto il CRC32 dell'header
            _size, pos = _read_vint(head, pos)
            htype, pos = _read_vint(head, pos)
            if htype != 1:
                return False
            hflags, pos = _read_vint(head, pos)
            if hflags & 0x01:
                _extra, pos = _read_vint(head, pos)
            if hflags & 0x02:
                _data, pos = _read_vint(head, pos)
            arc_flags, _ = _read_vint(head, pos)
            return bool(arc_flags & 0x04)
        if head.startswith(_RAR4_MAGIC):
            off = len(_RAR4_MAGIC)
            if head[off + 2] != 0x73:
                return False
            flags = int.from_bytes(head[off + 3:off + 5], "little")
            return bool(flags & 0x0008)
    except (OSError, ValueError, IndexError):
        pass
    return False


class RarComicArchive(ComicArchive):
    """RAR tramite `unrar`: `lb` per l'indice, `p` per estrarre una sola voce su stdout."""

    kind = "RAR"

    def __init__(self, path: str):
        super().__init__(path)
        self._unrar = shutil.which("unrar")
        if not self._unrar:
            raise ArchiveError("Per aprire i CBR è necessario 'unrar' (dnf install unrar).")
        # -p- evita che unrar resti bloccato a chiedere una password su stdin.
        names = _decode_names(_run([self._unrar, "lb", "-p-", "--", path], _LIST_TIMEOUT_S))
        self.pages = sort_pages([PageEntry(n, 0) for n in names if is_page_name(n)])
        self.solid = detect_rar_solid(path)

    def read_page(self, index: int) -> bytes:
        entry = self._entry(index)
        # Il nome completo di percorso viene confrontato in modo esatto da unrar (verificato:
        # il solo basename non corrisponde), quindi non c'è rischio di concatenare più voci.
        data = _run([self._unrar, "p", "-inul", "-p-", "--", self.path, entry.name], _EXTRACT_TIMEOUT_S)
        if not data:
            raise ArchiveError(f"Pagina vuota o non trovata: {entry.name}")
        return data


class SevenZipComicArchive(ComicArchive):
    """CB7 tramite `7z`: `l -slt` per l'indice, `e -so -spd` per la singola voce."""

    kind = "7Z"

    def __init__(self, path: str):
        super().__init__(path)
        self._7z = shutil.which("7z") or shutil.which("7za") or shutil.which("7zz")
        if not self._7z:
            raise ArchiveError("Per aprire i CB7 è necessario '7z' (dnf install 7zip).")
        raw = _run([self._7z, "l", "-slt", "-ba", "-sccUTF-8", "--", path], _LIST_TIMEOUT_S)
        entries, solid = [], False
        for block in raw.decode("utf-8", "surrogateescape").split("\n\n"):
            fields = dict(
                ln.split(" = ", 1) for ln in block.splitlines() if " = " in ln
            )
            name = fields.get("Path")
            if not name or fields.get("Folder") == "+" or "D" in fields.get("Attributes", "")[:1]:
                continue
            if is_page_name(name):
                entries.append(PageEntry(name, int(fields.get("Size") or 0)))
        for line in raw.decode("utf-8", "replace").splitlines():
            if line.startswith("Solid = +"):
                solid = True
        self.pages = sort_pages(entries)
        self.solid = solid

    def read_page(self, index: int) -> bytes:
        entry = self._entry(index)
        # -spd disattiva l'interpretazione dei caratteri jolly nel nome della voce.
        data = _run(
            [self._7z, "e", "-so", "-spd", "-bd", "-p-", "--", self.path, entry.name],
            _EXTRACT_TIMEOUT_S,
        )
        if not data:
            raise ArchiveError(f"Pagina vuota o non trovata: {entry.name}")
        return data


def sniff_format(path: str) -> str:
    """Riconosce il formato dai magic bytes: molti .cbr sono in realtà ZIP e viceversa."""
    with open(path, "rb") as fh:
        head = fh.read(8)
    if head.startswith(_ZIP_MAGICS):
        return "zip"
    if head.startswith(_RAR4_MAGIC) or head.startswith(_RAR5_MAGIC):
        return "rar"
    if head.startswith(_7Z_MAGIC):
        return "7z"
    ext = os.path.splitext(path)[1].lower()
    return {".cbz": "zip", ".zip": "zip", ".cbr": "rar", ".rar": "rar", ".cb7": "7z", ".7z": "7z"}.get(ext, "")


def open_archive(path: str) -> ComicArchive:
    if not os.path.isfile(path):
        raise ArchiveError(f"File non trovato: {path}")
    t0 = time.perf_counter()
    try:
        fmt = sniff_format(path)
    except OSError as exc:
        raise ArchiveError(f"Impossibile leggere {path}: {exc}") from exc
    if fmt == "zip":
        archive: ComicArchive = ZipComicArchive(path)
    elif fmt == "rar":
        archive = RarComicArchive(path)
    elif fmt == "7z":
        archive = SevenZipComicArchive(path)
    else:
        raise ArchiveError("Formato non riconosciuto (attesi CBZ, CBR o CB7).")
    archive.index_time_ms = (time.perf_counter() - t0) * 1000.0
    if not archive.pages:
        archive.close()
        raise ArchiveError("L'archivio non contiene immagini.")
    return archive
