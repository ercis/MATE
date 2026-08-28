#!/usr/bin/env bash
#
# Register the CONFIDENTIAL service-account client the API uses to DELETE users
# from Keycloak (the admin "delete user" flow), on a *running* Keycloak. Use
# this instead of editing the realm-export JSON when Keycloak already has a
# populated DB – the JSON is only imported on first boot (empty DB), so on a
# re-deploy it is ignored.
#
# What it does (all idempotent – safe to re-run):
#   1. Creates/updates the confidential client `flows-funds-admin`: a service
#      account (client_credentials grant) only – no browser/standard flow, no
#      direct-access grants, fullScopeAllowed OFF.
#   2. Grants its service account ONLY the `realm-management` client role
#      `manage-users` (least privilege – it can manage users, nothing else).
#   3. Prints the generated client secret + the four env vars to add to the VM
#      `.env`.
#
# The API no-ops the delete's Keycloak step when these vars are unset, so this
# is only needed where a user delete should ALSO remove the Keycloak account.
# The secret is powerful (it can delete any realm user) – keep it in `.env`
# only, never in git or the console.
#
# Requirements: docker (Keycloak running in a container), python3 on the host.
#
# Usage (local compose):
#   ./infra/keycloak/configure-admin-client.sh
#
# Usage (uni VM – Keycloak lives under /auth there):
#   KC_SERVER=http://localhost:8080/auth ./infra/keycloak/configure-admin-client.sh
#
set -euo pipefail

# ── Config (override via env) ────────────────────────────────────────────────
KC_CONTAINER="${KC_CONTAINER:-mate-keycloak}"
REALM="${REALM:-flows-funds}"
# The confidential client id. Must match KEYCLOAK_ADMIN_CLIENT_ID in the VM .env.
ADMIN_CLIENT_ID="${ADMIN_CLIENT_ID:-flows-funds-admin}"
# Internal admin endpoint for kcadm (host -> KC). In prod KC serves under /auth,
# so set KC_SERVER=http://localhost:8080/auth there.
KC_SERVER="${KC_SERVER:-http://localhost:8080}"
KC_ADMIN="${KEYCLOAK_ADMIN:-admin}"
KC_ADMIN_PASSWORD="${KEYCLOAK_ADMIN_PASSWORD:-admin}"
# What the API (container -> container) should use as the admin base URL. This
# is the docker-network hostname, NOT KC_SERVER (which is the host's view).
API_KC_BASE_URL="${API_KC_BASE_URL:-http://keycloak:8080/auth}"

KCADM=/opt/keycloak/bin/kcadm.sh
kc()      { docker exec     "$KC_CONTAINER" "$KCADM" "$@"; }
kc_pipe() { docker exec -i  "$KC_CONTAINER" "$KCADM" "$@"; }

if ! docker ps --format '{{.Names}}' | grep -qx "$KC_CONTAINER"; then
  echo "✗ container '$KC_CONTAINER' is not running (set KC_CONTAINER)." >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "✗ python3 is required on the host." >&2
  exit 1
fi

# ── 1. Authenticate kcadm ────────────────────────────────────────────────────
echo "→ Authenticating kcadm against $KC_SERVER (realm master)"
kc config credentials --server "$KC_SERVER" --realm master \
  --user "$KC_ADMIN" --password "$KC_ADMIN_PASSWORD"

# ── 2. Create/update the confidential service-account client ──────────────────
client_json() {
  ADMIN_CLIENT_ID="$ADMIN_CLIENT_ID" python3 - <<'PY'
import json, os
print(json.dumps({
    "clientId": os.environ["ADMIN_CLIENT_ID"],
    "name": "Mate admin (user management)",
    "description": "Confidential service-account client the API uses to delete Keycloak users.",
    "protocol": "openid-connect",
    "enabled": True,
    # Confidential + service account only: client_credentials grant, no browser
    # flow, no direct grants. Security rests on the secret (kept in the API .env).
    "publicClient": False,
    "serviceAccountsEnabled": True,
    "standardFlowEnabled": False,
    "implicitFlowEnabled": False,
    "directAccessGrantsEnabled": False,
    # Token carries only explicitly mapped roles (least privilege).
    "fullScopeAllowed": False,
}))
PY
}

client_id_of() {  # clientId -> internal uuid ('' if absent)
  kc get clients -r "$REALM" -q clientId="$1" --fields id \
    | python3 -c "import sys,json;rows=json.load(sys.stdin);print(rows[0]['id'] if rows else '')"
}

CID="$(client_id_of "$ADMIN_CLIENT_ID")"
if [ -n "$CID" ]; then
  echo "→ Updating client '$ADMIN_CLIENT_ID' ($CID)"
  client_json | kc_pipe update "clients/$CID" -r "$REALM" -f -
else
  echo "→ Creating client '$ADMIN_CLIENT_ID'"
  client_json | kc_pipe create clients -r "$REALM" -f -
  CID="$(client_id_of "$ADMIN_CLIENT_ID")"
fi
if [ -z "$CID" ]; then
  echo "✗ client '$ADMIN_CLIENT_ID' not found after create – aborting." >&2
  exit 1
fi

# ── 3. Grant the service account ONLY realm-management:manage-users ───────────
# Least privilege: it can create/delete/update realm users and nothing else.
SA_USER="service-account-${ADMIN_CLIENT_ID}"
echo "→ Granting '$SA_USER' the realm-management role 'manage-users'"
kc add-roles -r "$REALM" --uusername "$SA_USER" \
  --cclientid realm-management --rolename manage-users

# ── 4. Read back the client secret ───────────────────────────────────────────
SECRET="$(kc get "clients/$CID/client-secret" -r "$REALM" \
  | python3 -c "import sys,json;print(json.load(sys.stdin).get('value',''))")"

# ── Summary ──────────────────────────────────────────────────────────────────
echo
echo "✓ Done. Client '$ADMIN_CLIENT_ID' in realm '$REALM':"
echo "    confidential, service account only, fullScopeAllowed off"
echo "    service-account role → realm-management:manage-users"
echo
echo "Add to the VM's .env (then recreate the api container – 'restart' does NOT"
echo "re-read .env):"
echo "    KEYCLOAK_ADMIN_BASE_URL=$API_KC_BASE_URL"
echo "    KEYCLOAK_REALM=$REALM"
echo "    KEYCLOAK_ADMIN_CLIENT_ID=$ADMIN_CLIENT_ID"
echo "    KEYCLOAK_ADMIN_CLIENT_SECRET=$SECRET"
echo "    docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d api"
