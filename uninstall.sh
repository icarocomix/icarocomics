#!/usr/bin/env bash
# Rimuove IcaroComics installato con install.sh e ripristina le associazioni MIME precedenti.
# Le preferenze utente (~/.config/IcaroComics) vengono conservate salvo --purge.
set -euo pipefail

APP_ID="icarocomics"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
INSTALL_DIR="$DATA_HOME/$APP_ID"
APPS_DIR="$DATA_HOME/applications"
prev_file="$INSTALL_DIR/previous-defaults"

if [[ -f "$prev_file" ]]; then
    while IFS='=' read -r mt app; do
        [[ -n "$mt" && -n "$app" && "$app" != "$APP_ID.desktop" ]] && xdg-mime default "$app" "$mt"
    done < "$prev_file"
fi

rm -f "$HOME/.local/bin/$APP_ID" "$APPS_DIR/$APP_ID.desktop" \
      "$DATA_HOME/icons/hicolor/scalable/apps/$APP_ID.svg"
rm -rf "$INSTALL_DIR"
# Tolgo le righe residue che puntano a IcaroComics dal file delle associazioni utente.
mimeapps="${XDG_CONFIG_HOME:-$HOME/.config}/mimeapps.list"
[[ -f "$mimeapps" ]] && sed -i "/=$APP_ID\.desktop;\?$/d; s/$APP_ID\.desktop;//g" "$mimeapps"
update-desktop-database -q "$APPS_DIR" 2>/dev/null || true
gtk-update-icon-cache -q -t -f "$DATA_HOME/icons/hicolor" 2>/dev/null || true

if [[ "${1:-}" == "--purge" ]]; then
    rm -rf "${XDG_CONFIG_HOME:-$HOME/.config}/IcaroComics"
fi
echo "IcaroComics rimosso."
