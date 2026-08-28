#!/usr/bin/env bash
#
# Register the public OAuth client that external MCP clients (Claude Code,
# Claude Desktop / claude.ai, Codex, MCP Inspector, …) use to log in to the
# Mate MCP server, on a *running* Keycloak. Use this instead of editing the
# realm-export JSON when Keycloak already has a populated DB – the JSON is only
# imported on first boot (empty DB), so on a re-deploy it is ignored.
#
# What it does (all idempotent – safe to re-run):
#   1. Creates/updates the PUBLIC client `mate-mcp`: auth-code + PKCE (S256),
#      no client secret, no direct-access grants, no service account,
#      consent screen ON (a public client with loopback redirect URIs would
#      otherwise mint tokens silently off an existing SSO session),
#      fullScopeAllowed OFF (the token only carries what is explicitly mapped).
#   2. Adds the `flows-funds-api` audience protocol mapper – same JSON shape as
#      the flows-funds-web client's. Without it the access token lacks the
#      `aud` the API validates and every /mcp call 401s.
#   3. Creates one realm CLIENT SCOPE per MCP scope and attaches them to the
#      client: the `*:read` scopes as DEFAULT (always in the token), the
#      write/manage/control scopes + `admin` as OPTIONAL (only when the MCP
#      client asks for them). The scope ids MUST stay in sync with
#      apps/api/src/mate/api/mcp/scopes.py – the API maps the JWT `scope`
#      claim onto exactly these strings.
#   4. Ensures the `admin` realm role exists and maps it onto the `admin`
#      client scope. fullScopeAllowed=false strips realm roles from the token;
#      this scope-mapping re-adds `admin` to `realm_access.roles` only when the
#      admin scope is requested – the API requires scope AND role for the
#      admin toolset.
#
# The script OWNS the client's redirectUris – it overwrites them on every run.
# Add extra ones via PUBLIC_BASE_URL / EXTRA_REDIRECT_URIS, not the console.
#
# Requirements: docker (Keycloak running in a container), python3 on the host.
#
# Usage (local compose):
#   ./infra/keycloak/configure-mcp-client.sh
#
# Usage (uni VM – Keycloak lives under /auth there):
#   KC_SERVER=http://localhost:8080/auth \
#   PUBLIC_BASE_URL=https://pm-mate.uni-muenster.de \
#     ./infra/keycloak/configure-mcp-client.sh
#
set -euo pipefail

# ── Config (override via env) ────────────────────────────────────────────────
KC_CONTAINER="${KC_CONTAINER:-mate-keycloak}"
REALM="${REALM:-flows-funds}"
# The OAuth client id. Must match MCP_OAUTH_CLIENT_ID in the VM .env.
MCP_CLIENT_ID="${MCP_CLIENT_ID:-mate-mcp}"
# Public origin of the deployment (e.g. https://pm-mate.uni-muenster.de).
# When set, "${PUBLIC_BASE_URL}/*" is added to the redirect URIs so browser-
# based clients hosted on the app origin can complete the flow.
PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-}"
# Extra redirect URIs, space-separated (e.g. another connector's callback).
EXTRA_REDIRECT_URIS="${EXTRA_REDIRECT_URIS:-}"
# Audience the API validates on access tokens (KEYCLOAK_AUDIENCE).
API_AUDIENCE="${API_AUDIENCE:-flows-funds-api}"
# Internal admin endpoint. In prod Keycloak serves under /auth – set
# KC_SERVER=http://localhost:8080/auth then (KC_HTTP_RELATIVE_PATH=/auth).
KC_SERVER="${KC_SERVER:-http://localhost:8080}"
KC_ADMIN="${KEYCLOAK_ADMIN:-admin}"
KC_ADMIN_PASSWORD="${KEYCLOAK_ADMIN_PASSWORD:-admin}"

# ── MCP scope taxonomy ───────────────────────────────────────────────────────
# MUST match apps/api/src/mate/api/mcp/scopes.py exactly (READ_SCOPES /
# WRITE_SCOPES / SCOPE_ADMIN). Attached as DEFAULT → always in the token;
# OPTIONAL → only when the client requests them in the `scope` parameter.
READ_SCOPES=(
  "processes:read"
  "modules:read"
  "dashboards:read"
  "jobs:read"
  "watched:read"
  "account:read"
)
OPTIONAL_SCOPES=(
  "processes:write"
  "modules:write"
  "modules:manage"
  "dashboards:write"
  "jobs:control"
  "watched:write"
  "account:write"
  "admin"
)

KCADM=/opt/keycloak/bin/kcadm.sh

# kc: run kcadm in the container, output to host stdout.
kc()      { docker exec     "$KC_CONTAINER" "$KCADM" "$@"; }
# kc_pipe: same, but forwards host stdin (for `-f -` reads).
kc_pipe() { docker exec -i  "$KC_CONTAINER" "$KCADM" "$@"; }

if ! docker ps --format '{{.Names}}' | grep -qx "$KC_CONTAINER"; then
  echo "✗ container '$KC_CONTAINER' is not running (set KC_CONTAINER)." >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "✗ python3 is required on the host." >&2
  exit 1
fi

# ── 1. Authenticate kcadm (stored in the container for subsequent calls) ─────
echo "→ Authenticating kcadm against $KC_SERVER (realm master)"
kc config credentials --server "$KC_SERVER" --realm master \
  --user "$KC_ADMIN" --password "$KC_ADMIN_PASSWORD"

# ── 2. Create/update the public MCP client ───────────────────────────────────
client_json() {
  MCP_CLIENT_ID="$MCP_CLIENT_ID" PUBLIC_BASE_URL="$PUBLIC_BASE_URL" \
  EXTRA_REDIRECT_URIS="$EXTRA_REDIRECT_URIS" \
  python3 - <<'PY'
import json, os
# Loopback wildcards cover CLI/desktop MCP clients (RFC 8252 §7.3 loopback
# redirect – the port is random, so it must be a wildcard). claude.ai's fixed
# callback covers Claude Desktop / claude.ai custom connectors.
redirect_uris = [
    "http://localhost/*",
    "http://127.0.0.1/*",
    "https://claude.ai/api/mcp/auth_callback",
]
base = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
if base:
    redirect_uris.append(f"{base}/*")
redirect_uris += [u for u in os.environ.get("EXTRA_REDIRECT_URIS", "").split() if u]
print(json.dumps({
    "clientId": os.environ["MCP_CLIENT_ID"],
    "name": "Mate MCP",
    "description": "Public OAuth client for external MCP clients (auth-code + PKCE).",
    "protocol": "openid-connect",
    "enabled": True,
    # Public client: no secret to distribute; security rests on PKCE (S256),
    # the exact redirect URIs and the consent screen.
    "publicClient": True,
    "standardFlowEnabled": True,
    "implicitFlowEnabled": False,
    "directAccessGrantsEnabled": False,
    "serviceAccountsEnabled": False,
    # A public client + loopback redirects could otherwise silently obtain a
    # token off an existing Keycloak SSO session. The consent screen makes the
    # user approve each MCP client (once, per scope set).
    "consentRequired": True,
    # Token only carries explicitly mapped roles/scopes (least privilege).
    "fullScopeAllowed": False,
    "redirectUris": redirect_uris,
    "webOrigins": ["+"],
    "attributes": {"pkce.code.challenge.method": "S256"},
}))
PY
}

client_id_of() {  # clientId -> internal uuid ('' if absent)
  kc get clients -r "$REALM" -q clientId="$1" --fields id \
    | python3 -c "import sys,json;rows=json.load(sys.stdin);print(rows[0]['id'] if rows else '')"
}

CID="$(client_id_of "$MCP_CLIENT_ID")"
if [ -n "$CID" ]; then
  echo "→ Updating client '$MCP_CLIENT_ID' ($CID)"
  client_json | kc_pipe update "clients/$CID" -r "$REALM" -f -
else
  echo "→ Creating client '$MCP_CLIENT_ID'"
  client_json | kc_pipe create clients -r "$REALM" -f -
  CID="$(client_id_of "$MCP_CLIENT_ID")"
fi
if [ -z "$CID" ]; then
  echo "✗ client '$MCP_CLIENT_ID' not found after create – aborting." >&2
  exit 1
fi

# ── 3. Audience protocol mapper (same shape as flows-funds-web's) ────────────
# Without `aud: flows-funds-api` the API's JWT validation rejects the token.
MAPPER_NAME="${API_AUDIENCE}-audience"
mapper_json() {
  MAPPER_NAME="$MAPPER_NAME" API_AUDIENCE="$API_AUDIENCE" MAPPER_ID="${1:-}" \
  python3 - <<'PY'
import json, os
rep = {
    "name": os.environ["MAPPER_NAME"],
    "protocol": "openid-connect",
    "protocolMapper": "oidc-audience-mapper",
    "consentRequired": False,
    "config": {
        "included.client.audience": os.environ["API_AUDIENCE"],
        "id.token.claim": "false",
        "access.token.claim": "true",
    },
}
if os.environ.get("MAPPER_ID"):
    rep["id"] = os.environ["MAPPER_ID"]
print(json.dumps(rep))
PY
}

MID="$(kc get "clients/$CID/protocol-mappers/models" -r "$REALM" \
  | MAPPER_NAME="$MAPPER_NAME" python3 -c "
import sys,json,os
for m in json.load(sys.stdin):
    if m.get('name')==os.environ['MAPPER_NAME']:
        print(m['id']); break")"
if [ -n "$MID" ]; then
  echo "→ Updating audience mapper '$MAPPER_NAME'"
  mapper_json "$MID" | kc_pipe update "clients/$CID/protocol-mappers/models/$MID" -r "$REALM" -f -
else
  echo "→ Creating audience mapper '$MAPPER_NAME'"
  mapper_json | kc_pipe create "clients/$CID/protocol-mappers/models" -r "$REALM" -f -
fi

# ── 4. Realm client scopes, one per MCP scope ────────────────────────────────
scope_json() {  # $1 = scope name, $2 = optional existing id
  SCOPE_NAME="$1" SCOPE_ID="${2:-}" python3 - <<'PY'
import json, os
# Consent-screen texts – keep in sync with SCOPE_DESCRIPTIONS in
# apps/api/src/mate/api/mcp/scopes.py.
DESCRIPTIONS = {
    "processes:read": "List processes (event logs) and read their aggregate stats.",
    "processes:write": "Import, rename, filter, remap, duplicate and delete processes.",
    "modules:read": "Read module analysis outputs and datasets for your processes.",
    "modules:write": "Change per-module configuration (enable/disable, settings).",
    "modules:manage": "Uninstall modules and restore default modules.",
    "dashboards:read": "List and read dashboards (own and shared with you).",
    "dashboards:write": "Create, edit, share and delete dashboards.",
    "jobs:read": "List background jobs and read their status/progress.",
    "jobs:control": "Cancel/retry jobs and pause/resume your queue.",
    "watched:read": "List watched import folders and their file ledger.",
    "watched:write": "Create, edit, scan and delete watched import folders.",
    "account:read": "Read your usage summary and API-token list.",
    "account:write": "Revoke your own API tokens.",
    "admin": "Platform administration (OAuth + admin realm role only).",
}
name = os.environ["SCOPE_NAME"]
rep = {
    "name": name,
    "description": DESCRIPTIONS.get(name, ""),
    "protocol": "openid-connect",
    "attributes": {
        # The whole point: the scope name lands in the JWT `scope` claim,
        # which the API maps onto the MCP taxonomy.
        "include.in.token.scope": "true",
        "display.on.consent.screen": "true",
        "consent.screen.text": DESCRIPTIONS.get(name, name),
    },
}
if os.environ.get("SCOPE_ID"):
    rep["id"] = os.environ["SCOPE_ID"]
print(json.dumps(rep))
PY
}

scope_map() {  # all realm client scopes as "name<TAB>id" lines
  kc get client-scopes -r "$REALM" --fields id,name \
    | python3 -c "import sys,json
for s in json.load(sys.stdin): print(f\"{s['name']}\t{s['id']}\")"
}

lookup() {  # $1 = name, stdin-free lookup in $SCOPES_TSV
  printf '%s\n' "$SCOPES_TSV" | awk -F'\t' -v n="$1" '$1==n {print $2; exit}'
}

SCOPES_TSV="$(scope_map)"
for scope in "${READ_SCOPES[@]}" "${OPTIONAL_SCOPES[@]}"; do
  sid="$(lookup "$scope")"
  if [ -n "$sid" ]; then
    echo "→ Updating client scope '$scope'"
    scope_json "$scope" "$sid" | kc_pipe update "client-scopes/$sid" -r "$REALM" -f -
  else
    echo "→ Creating client scope '$scope'"
    scope_json "$scope" | kc_pipe create client-scopes -r "$REALM" -f -
  fi
done
SCOPES_TSV="$(scope_map)"   # re-read: pick up ids of freshly created scopes

# ── 5. Attach the scopes to the client (default vs optional) ─────────────────
# `kcadm update -n` = plain PUT without the read-merge GET (these endpoints
# have no GET-by-id). Re-running is a no-op; a scope sitting in the wrong list
# is moved.
attached() {  # $1 = default|optional -> names, one per line
  kc get "clients/$CID/$1-client-scopes" -r "$REALM" --fields name \
    | python3 -c "import sys,json
for s in json.load(sys.stdin): print(s['name'])"
}
DEFAULT_ATTACHED="$(attached default)"
OPTIONAL_ATTACHED="$(attached optional)"

attach() {  # $1 = scope name, $2 = default|optional, $3 = other list name
  local sid; sid="$(lookup "$1")"
  if [ -z "$sid" ]; then
    echo "✗ client scope '$1' vanished mid-run – aborting." >&2
    exit 1
  fi
  if printf '%s\n' "$4" | grep -qx "$1"; then
    echo "  moving '$1' out of the $3 list"
    kc delete "clients/$CID/$3-client-scopes/$sid" -r "$REALM"
  fi
  if ! printf '%s\n' "$5" | grep -qx "$1"; then
    kc update "clients/$CID/$2-client-scopes/$sid" -r "$REALM" -n
    echo "  attached '$1' as $2"
  else
    echo "  '$1' already attached as $2"
  fi
}

echo "→ Attaching read scopes as DEFAULT client scopes"
for scope in "${READ_SCOPES[@]}"; do
  attach "$scope" default optional "$OPTIONAL_ATTACHED" "$DEFAULT_ATTACHED"
done
echo "→ Attaching write/manage/control + admin scopes as OPTIONAL client scopes"
for scope in "${OPTIONAL_SCOPES[@]}"; do
  attach "$scope" optional default "$DEFAULT_ATTACHED" "$OPTIONAL_ATTACHED"
done

# ── 6. Map the `admin` realm role onto the `admin` client scope ──────────────
# fullScopeAllowed=false strips realm roles from the token. The API's admin
# toolset needs BOTH the `admin` scope in `scope` AND `admin` inside
# realm_access.roles – this scope-mapping re-adds the role exactly when the
# admin scope is granted. The role itself is created if missing (it's the same
# role that gates /admin/* in the web app; membership is still assigned by an
# operator, never by this script).
if ! kc get "roles/admin" -r "$REALM" >/dev/null 2>&1; then
  echo "→ Creating missing realm role 'admin'"
  kc create roles -r "$REALM" -s name=admin -s 'description=Mate platform administrator'
fi
ADMIN_ROLE_ID="$(kc get "roles/admin" -r "$REALM" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")"
ADMIN_SCOPE_ID="$(lookup admin)"
if kc get "client-scopes/$ADMIN_SCOPE_ID/scope-mappings/realm" -r "$REALM" \
  | python3 -c "
import sys,json
raise SystemExit(0 if any(r.get('name')=='admin' for r in json.load(sys.stdin)) else 1)"; then
  echo "→ Realm role 'admin' already mapped onto the 'admin' client scope"
else
  echo "→ Mapping realm role 'admin' onto the 'admin' client scope"
  printf '[{"id":"%s","name":"admin"}]' "$ADMIN_ROLE_ID" \
    | kc_pipe create "client-scopes/$ADMIN_SCOPE_ID/scope-mappings/realm" -r "$REALM" -f -
fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo
echo "✓ Done. Client '$MCP_CLIENT_ID' in realm '$REALM':"
echo "    public + PKCE(S256), consent on, fullScopeAllowed off"
echo "    audience mapper  → aud: $API_AUDIENCE"
echo "    default scopes   → ${READ_SCOPES[*]}"
echo "    optional scopes  → ${OPTIONAL_SCOPES[*]}"
if [ -n "$PUBLIC_BASE_URL" ]; then
  echo "    redirect URIs    → loopback + claude.ai + ${PUBLIC_BASE_URL}/*"
else
  echo "    redirect URIs    → loopback + claude.ai (set PUBLIC_BASE_URL to add the app origin)"
fi
echo
echo "Next, in the VM's .env (then recreate the api container – 'restart' does"
echo "NOT re-read .env):"
echo "    MCP_ENABLED=1"
echo "    API_BASE_URL=${PUBLIC_BASE_URL:-https://<public-origin>}"
echo "    MCP_OAUTH_CLIENT_ID=$MCP_CLIENT_ID"
echo "    docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d api"
