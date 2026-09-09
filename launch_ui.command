#!/bin/bash
# launch_ui.command — lance le serveur web Ylos Pipeline et ouvre l'UI dans le navigateur.
# Double-clic dans le Finder (ou icône dans le Dock) : plus besoin de retaper la commande.
#
# Comportement :
#   - si un serveur tourne déjà sur le port, ouvre juste le navigateur dessus
#   - sinon démarre `python3 ylos_ui.py` (garde le projet actif déjà persisté dans
#     ~/.ylos/active_project — rien n'est réinitialisé) et ouvre le navigateur ~1s après
#   - Ctrl-C (ou fermer la fenêtre Terminal) arrête le serveur, comme en lancement manuel
#
# Chemin résolu depuis l'emplacement du script (pas de chemin absolu en dur) : le lanceur
# reste valide si le repo est déplacé, même principe que $PROJ_ROOT (cf. README).

set -e
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
PORT=8765
URL="http://127.0.0.1:${PORT}/"

cd "$REPO_DIR"

if curl -s -o /dev/null "$URL"; then
    echo "[ylos] Serveur deja lance sur $URL - ouverture du navigateur."
    open "$URL"
    exit 0
fi

echo "[ylos] Demarrage du serveur (port $PORT) depuis $REPO_DIR ..."
( sleep 1 && open "$URL" ) &

python3 ylos_ui.py --port "$PORT"
