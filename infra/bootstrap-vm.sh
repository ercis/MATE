#!/usr/bin/env bash
#
# One-shot VM bootstrap: does the whole first-time setup (DEPLOY.md §2–§5).
#   1. Generates .env with fresh secrets (skipped if .env already exists).
#   2. Patches the Keycloak realm for the prod domain – redirect URI, web
#      origin, post-logout URIs, rootUrl/baseUrl, and the client secret (kept
#      in sync with .env so the login can't break on a mismatch).
#   3. Starts the stack (docker compose … up -d --build) – unless --no-start.
#
# Run ONCE in the repo root on the VM, the very first time:
#   ./infra/bootstrap-vm.sh                # set up AND start
#   ./infra/bootstrap-vm.sh --no-start     # only write .env + patch realm
#
# Idempotent: safe to re-run. Override the domain with PUBLIC_URL=… if needed.
#
# Note: this edits the git-tracked realm JSON. `make deploy` does
# `git reset --hard`, which would revert it – that's harmless once Keycloak has
# imported the realm on first boot (it then lives in the Keycloak DB and the
# JSON is ignored). Only re-run this if you ever `docker compose down -v`.

set -euo pipefail
cd "$(dirname "$0")/.."   # repo root

REALM="infra/keycloak/realm-export/flows-funds-realm.json"
PUBLIC_URL="${PUBLIC_URL:-https://pm-mate.uni-muenster.de}"
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.prod.yml)

start=1
[[ "${1:-}" == "--no-start" ]] && start=0

command -v jq >/dev/null     || { echo "✗ jq fehlt – installieren mit: sudo apt install -y jq"; exit 1; }
command -v openssl >/dev/null || { echo "✗ openssl fehlt – sudo apt install -y openssl"; exit 1; }
[[ -f "$REALM" ]]            || { echo "✗ $REALM nicht gefunden – im Repo-Root ausführen."; exit 1; }

# ── 1. Secrets / .env ────────────────────────────────────────────────────────
if [[ -f .env ]]; then
  echo "• .env existiert bereits – Secrets bleiben unverändert."
  CLIENT_SECRET="$(grep -E '^KEYCLOAK_CLIENT_SECRET=' .env | cut -d= -f2-)"
  [[ -n "$CLIENT_SECRET" ]] || { echo "  ✗ KEYCLOAK_CLIENT_SECRET fehlt in .env"; exit 1; }
else
  CLIENT_SECRET="$(openssl rand -base64 32)"
  cat > .env <<EOF
AUTH_SECRET=$(openssl rand -base64 32)
KEYCLOAK_CLIENT_SECRET=$CLIENT_SECRET
KEYCLOAK_DB_PASSWORD=$(openssl rand -base64 24)
KEYCLOAK_ADMIN=admin
KEYCLOAK_ADMIN_PASSWORD=$(openssl rand -base64 24)
EOF
  chmod 600 .env
  echo "• .env erstellt (chmod 600)."
fi

# ── 2. Realm für die Prod-Domain patchen (idempotent) ────────────────────────
tmp="$(mktemp)"
jq --arg s "$CLIENT_SECRET" --arg u "$PUBLIC_URL" '
  (.clients[] | select(.clientId == "flows-funds-web")) |= (
      .secret      = $s
    | .rootUrl     = $u
    | .baseUrl     = $u
    | .redirectUris = ([$u + "/api/auth/callback/keycloak"] + .redirectUris | unique)
    | .webOrigins   = ([$u] + .webOrigins | unique)
    | .attributes["post.logout.redirect.uris"] =
        (((.attributes["post.logout.redirect.uris"] // "") | split("##"))
          + [$u + "/login", $u + "/"]
          | map(select(length > 0)) | unique | join("##"))
  )
' "$REALM" > "$tmp" && mv "$tmp" "$REALM"
# mktemp creates the file 0600; the Keycloak container (uid 1000) must be able
# to read the bind-mounted realm, so make it world-readable.
chmod 644 "$REALM"
echo "• Realm gepatcht für $PUBLIC_URL (redirect URI, web origin, secret synchron mit .env)."

echo
echo "Keycloak-Admin-Login (https://…/auth/admin):"
echo "    user: $(grep '^KEYCLOAK_ADMIN=' .env | cut -d= -f2-)"
echo "    pass: $(grep '^KEYCLOAK_ADMIN_PASSWORD=' .env | cut -d= -f2-)"
echo

# ── 3. Stack starten ─────────────────────────────────────────────────────────
if [[ "$start" == "0" ]]; then
  echo "Konfiguration fertig (--no-start). Starten mit:"
  echo "    ${COMPOSE[*]} up -d --build"
  exit 0
fi

command -v docker >/dev/null || { echo "✗ docker fehlt – siehe DEPLOY.md, dann erneut ausführen."; exit 1; }
if ss -ltn 2>/dev/null | grep -q ':443 '; then
  echo "✗ Port 443 ist belegt – bitte den Dienst dort stoppen und dann starten:"
  echo "    ${COMPOSE[*]} up -d --build"
  exit 1
fi

echo "• Starte Stack – der erste Build kann ~10 Min dauern (cv4cdd zieht TensorFlow)…"
"${COMPOSE[@]}" up -d --build
echo
echo "✔ Läuft. Logs ansehen:  ${COMPOSE[*]} logs -f api"
echo "  Prüfen:              curl https://pm-mate.uni-muenster.de/health"
