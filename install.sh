#!/usr/bin/env bash
# Installazione per l'utente corrente di IcaroComics (nessun privilegio di root richiesto).
#   - codice in        ~/.local/share/icarocomics/app
#   - eseguibile in    ~/.local/bin/icarocomics
#   - launcher in      ~/.local/share/applications/icarocomics.desktop
#   - icona in         ~/.local/share/icons/hicolor/scalable/apps/icarocomics.svg
#   - associazioni MIME CBZ/CBR/CB7 registrate con xdg-mime come applicazione predefinita
# Lo script è idempotente: rilanciarlo aggiorna l'installazione esistente.
set -euo pipefail

APP_ID="icarocomics"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
INSTALL_DIR="$DATA_HOME/$APP_ID"
BIN_DIR="$HOME/.local/bin"
APPS_DIR="$DATA_HOME/applications"
ICON_DIR="$DATA_HOME/icons/hicolor/scalable/apps"
DESKTOP_FILE="$APPS_DIR/$APP_ID.desktop"
PYTHON="${PYTHON:-/usr/bin/python3}"
MIME_TYPES=(
    application/vnd.comicbook+zip
    application/x-cbz
    application/vnd.comicbook-rar
    application/x-cbr
    application/x-cb7
)

info()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m[attenzione]\033[0m %s\n' "$*" >&2; }
fail()  { printf '\033[1;31m[errore]\033[0m %s\n' "$*" >&2; exit 1; }

# --- 1. dipendenze --------------------------------------------------------------------
info "Verifica delle dipendenze"
[[ -x "$PYTHON" ]] || fail "Python 3 non trovato in $PYTHON"
missing=()
"$PYTHON" -c 'import PyQt6.QtWidgets, PyQt6.QtSvg' 2>/dev/null || missing+=("python3-pyqt6")
"$PYTHON" -c 'import PIL' 2>/dev/null || missing+=("python3-pillow")
if ((${#missing[@]})); then
    fail "Moduli Python mancanti: ${missing[*]}  ->  sudo dnf install ${missing[*]}"
fi
command -v unrar >/dev/null || warn "'unrar' non trovato: i CBR non si apriranno (abilita RPM Fusion nonfree e: sudo dnf install unrar)"
command -v 7z >/dev/null || command -v 7za >/dev/null || warn "'7z' non trovato: i CB7 non si apriranno (sudo dnf install 7zip)"

# --- 2. codice applicativo ---------------------------------------------------------------
info "Copia dell'applicazione in $INSTALL_DIR"
mkdir -p "$INSTALL_DIR/app" "$BIN_DIR" "$APPS_DIR" "$ICON_DIR"
# Sostituisco atomicamente il pacchetto: prima copio in una cartella temporanea accanto,
# poi rinomino, così un'istanza in esecuzione non vede mai un pacchetto a metà.
staging="$INSTALL_DIR/app/.${APP_ID}.new"
rm -rf "$staging"
cp -r "$SRC_DIR/$APP_ID" "$staging"
find "$staging" -name '__pycache__' -type d -prune -exec rm -rf {} +
rm -rf "$INSTALL_DIR/app/$APP_ID"
mv "$staging" "$INSTALL_DIR/app/$APP_ID"
"$PYTHON" -m compileall -q "$INSTALL_DIR/app/$APP_ID" >/dev/null || true

# --- 3. eseguibile -----------------------------------------------------------------------
info "Creazione dell'eseguibile $BIN_DIR/$APP_ID"
cat > "$BIN_DIR/$APP_ID" <<EOF
#!/bin/sh
# Launcher generato da install.sh di IcaroComics.
export PYTHONPATH="$INSTALL_DIR/app\${PYTHONPATH:+:\$PYTHONPATH}"
exec "$PYTHON" -m $APP_ID "\$@"
EOF
chmod 755 "$BIN_DIR/$APP_ID"

# --- 4. icona e file .desktop --------------------------------------------------------------
info "Installazione di icona e file .desktop"
install -m 644 "$SRC_DIR/$APP_ID/data/$APP_ID.svg" "$ICON_DIR/$APP_ID.svg"
mime_list="$(printf '%s;' "${MIME_TYPES[@]}")"
cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=IcaroComics
GenericName=Lettore di fumetti
Comment=Legge fumetti CBZ, CBR e CB7 anche di grandi dimensioni senza estrarli su disco
Exec=$BIN_DIR/$APP_ID %f
TryExec=$BIN_DIR/$APP_ID
Icon=$APP_ID
Terminal=false
StartupNotify=true
StartupWMClass=$APP_ID
Categories=Graphics;Viewer;
MimeType=$mime_list
Keywords=comic;comics;cbz;cbr;cb7;manga;fumetti;fumetto;
EOF
chmod 644 "$DESKTOP_FILE"
if command -v desktop-file-validate >/dev/null; then
    desktop-file-validate "$DESKTOP_FILE" || warn "desktop-file-validate ha segnalato problemi"
fi

# --- 5. associazioni MIME -----------------------------------------------------------------
info "Registrazione dei tipi MIME"
prev_file="$INSTALL_DIR/previous-defaults"
if [[ ! -f "$prev_file" ]]; then
    # Salvo le associazioni preesistenti una sola volta, per poterle ripristinare con uninstall.sh.
    for mt in "${MIME_TYPES[@]}"; do
        printf '%s=%s\n' "$mt" "$(xdg-mime query default "$mt" 2>/dev/null || true)"
    done > "$prev_file"
fi
for mt in "${MIME_TYPES[@]}"; do
    xdg-mime default "$APP_ID.desktop" "$mt"
done
update-desktop-database -q "$APPS_DIR" 2>/dev/null || true
gtk-update-icon-cache -q -t -f "$DATA_HOME/icons/hicolor" 2>/dev/null || true

# --- 6. riepilogo --------------------------------------------------------------------------
info "Verifica"
for mt in "${MIME_TYPES[@]}"; do
    printf '   %-32s -> %s\n' "$mt" "$(xdg-mime query default "$mt")"
done
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) warn "$BIN_DIR non è nel PATH: da terminale usa il percorso completo $BIN_DIR/$APP_ID" ;;
esac
info "IcaroComics installato. Doppio clic su un .cbz/.cbr in Nautilus per aprirlo."
