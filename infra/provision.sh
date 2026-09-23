#!/usr/bin/env bash
# Bootstrap d'un pod GPU loue (RunPod community, Vast, ou tout hote CUDA).
#
# Ce que fait ce script :
#   1. verifie le GPU et l'espace disque
#   2. installe Tailscale en mode userspace (pas de /dev/net/tun dans un
#      conteneur RunPod) et rattache le pod au tailnet
#   3. ferme tout acces reseau entrant hors tailnet
#   4. prepare les caches de modeles sur le volume persistant
#
# Le pod est traite comme du materiel non fiable : il recoit les modeles et le
# calcul, jamais l'historique de conversation ni le dataset vocal d'origine.
#
#   export TS_AUTHKEY=tskey-auth-xxxx
#   bash infra/provision.sh

set -euo pipefail

POD_NAME="${POD_NAME:-aigf-pod}"
WORKSPACE="${WORKSPACE:-/workspace}"

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

# --- 1. Materiel -------------------------------------------------------------
log "Verification du materiel"
command -v nvidia-smi >/dev/null || die "nvidia-smi absent : ce pod n'a pas de GPU NVIDIA utilisable"
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader

VRAM_MB=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -1)
if [ "${VRAM_MB}" -lt 20000 ]; then
    warn "Seulement ${VRAM_MB} Mio de VRAM. Le LLM 8B quantifie, le STT et le TTS"
    warn "ne tiendront pas ensemble. Prendre un 24 Go minimum, 48 Go confortable."
fi

FREE_GB=$(df -BG --output=avail "${WORKSPACE}" 2>/dev/null | tail -1 | tr -dc '0-9' || echo 0)
if [ "${FREE_GB:-0}" -lt 60 ]; then
    warn "Seulement ${FREE_GB} Gio libres sur ${WORKSPACE}. Prevoir 60 Gio :"
    warn "whisper large-v3 (3 Go) + LLM 8B Q4 (5 Go) + TTS (2-4 Go) + caches."
fi

# --- 2. Tailscale ------------------------------------------------------------
log "Installation de Tailscale"
if ! command -v tailscale >/dev/null; then
    curl -fsSL https://tailscale.com/install.sh | sh
fi

if ! pgrep -x tailscaled >/dev/null; then
    # userspace-networking : les conteneurs de GPU loue n'ont pas /dev/net/tun.
    tailscaled --tun=userspace-networking --state="${WORKSPACE}/.tailscale/state" \
        --socket=/var/run/tailscale/tailscaled.sock >/var/log/tailscaled.log 2>&1 &
    sleep 3
fi

if [ -z "${TS_AUTHKEY:-}" ]; then
    die "TS_AUTHKEY non defini. Creer une cle ephemere sur https://login.tailscale.com/admin/settings/keys"
fi

log "Rattachement au tailnet sous le nom ${POD_NAME}"
tailscale up \
    --authkey="${TS_AUTHKEY}" \
    --hostname="${POD_NAME}" \
    --ssh \
    --accept-routes=false \
    --advertise-tags="${TS_TAGS:-tag:aigf}" || \
    die "tailscale up a echoue. Verifier que la cle est valide et non expiree."

TS_IP=$(tailscale ip -4 | head -1)
log "Pod joignable sur ${TS_IP} (tailnet uniquement)"

# --- 3. Fermeture du reseau --------------------------------------------------
log "Verrouillage de l'acces entrant"

# Aucune commande de pare-feu n'est fatale. Un conteneur de GPU loue n'a
# generalement pas la capacite NET_ADMIN, donc ufw echoue meme s'il est installe.
# Avorter ici priverait le pod de la preparation des caches pour une protection
# qui, de toute facon, n'etait pas applicable.
firewall_ok=1
if command -v ufw >/dev/null 2>&1; then
    {
        ufw --force reset &&
        ufw default deny incoming &&
        ufw default allow outgoing &&
        { ufw allow in on tailscale0 || true; } &&
        ufw --force enable
    } >/dev/null 2>&1 || firewall_ok=0

    if [ "${firewall_ok}" -eq 1 ]; then
        log "ufw actif : entrant refuse hors tailscale0"
    else
        warn "ufw est installe mais n'a pas pu s'appliquer (capacite NET_ADMIN absente ?)."
    fi
else
    firewall_ok=0
    warn "ufw absent."
fi

if [ "${firewall_ok}" -eq 0 ]; then
    warn "Le pare-feu local n'est pas actif. L'etancheite repose donc entierement sur"
    warn "deux choses, a verifier a la main :"
    warn "  1. aucun port declare dans le template du fournisseur (RunPod les publie)"
    warn "  2. tous les services lies a 127.0.0.1, ce que verifie le controle ci-dessous"
fi

# Controle : aucun service ne doit ecouter sur 0.0.0.0.
if command -v ss >/dev/null; then
    EXPOSED=$(ss -ltnH 2>/dev/null | awk '{print $4}' | grep -E '^(0\.0\.0\.0|\*|\[::\]):' || true)
    if [ -n "${EXPOSED}" ]; then
        warn "Services en ecoute sur toutes les interfaces :"
        echo "${EXPOSED}" | sed 's/^/      /'
        warn "Les relier a 127.0.0.1 ou a ${TS_IP}."
    fi
fi

# --- 4. Caches ---------------------------------------------------------------
log "Preparation des caches sur le volume persistant"
mkdir -p "${WORKSPACE}/.cache/huggingface" "${WORKSPACE}/.cache/torch" "${WORKSPACE}/.cache/speechbrain"
cat >> ~/.bashrc <<EOF

# aigf
export HF_HOME=${WORKSPACE}/.cache/huggingface
export TORCH_HOME=${WORKSPACE}/.cache/torch
EOF

log "Pod pret."
echo
echo "  Depuis le laptop, tunnel vers la signalisation WebRTC :"
echo "    ssh -N -L 7860:localhost:7860 root@${TS_IP}"
echo
echo "  Rappel : a la destruction du pod, revoquer la cle dans la console Tailscale."
echo "  Le volume persistant contient des modeles, jamais de conversations."
