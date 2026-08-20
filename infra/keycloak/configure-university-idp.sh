#!/usr/bin/env bash
#
# Configure the "university" OIDC identity provider on a *running* Keycloak and
# make logins skip Keycloak's own login form. Use this instead of editing the
# realm-export JSON when Keycloak already has a populated DB – the JSON is only
# imported on first boot (empty DB), so on a re-deploy it is ignored.
#
# What it does (all idempotent – safe to re-run):
#   1. Creates/updates the `university` OIDC identity provider (trustEmail=on).
#   2. Sets the browser flow's "Identity Provider Redirector" default provider
#      to `university` – anyone hitting Keycloak directly is bounced to the IdP.
#   3. Disables the "Review Profile" step in the "first broker login" flow so a
#      new user is provisioned silently (no profile-confirmation page).
#
# The app-side kc_idp_hint (KEYCLOAK_IDP_HINT=university, see auth.ts) already
# skips the form for logins that start from the web app; step 2 is the
# belt-and-suspenders for direct Keycloak hits.
#
# Requirements: docker (Keycloak running in a container), python3 + curl on the
# host. Admin console runs against the `master` realm and is NOT affected.
#
# Usage:
#   UNIV_CLIENT_ID=... UNIV_CLIENT_SECRET=... \
#   UNIV_DISCOVERY_URL=https://idp.uni-muenster.de/.../.well-known/openid-configuration \
#     ./infra/keycloak/configure-university-idp.sh
#
# Or pass the endpoints explicitly instead of a discovery URL:
#   UNIV_AUTH_URL, UNIV_TOKEN_URL, UNIV_JWKS_URL, UNIV_USERINFO_URL, UNIV_ISSUER
#
set -euo pipefail

# ── Config (override via env) ────────────────────────────────────────────────
KC_CONTAINER="${KC_CONTAINER:-mate-keycloak}"
REALM="${REALM:-flows-funds}"
# Alias MUST equal the segment in the registered broker redirect URI
# (.../broker/keycloak-oidc/endpoint) – changing it would require re-registering
# the redirect URI with the university IT centre.
IDP_ALIAS="${IDP_ALIAS:-keycloak-oidc}"
IDP_DISPLAY="${IDP_DISPLAY:-University Login}"
# Provider type. Use the GENERIC "oidc" ("OpenID Connect v1.0"), NOT
# "keycloak-oidc": KeycloakOIDCIdentityProvider's constructor hardcodes
# config.setAccessTokenJwt(true), so it parses the IdP's access token as a JWT
# regardless of the isAccessTokenJWT config below – fatal for WWU, which issues
# OPAQUE access tokens ("Failed to parse JWT header"). The generic oidc type
# honours isAccessTokenJWT=false and reads identity from the userinfo endpoint.
# The type is fixed at creation – Keycloak ignores it on update, so to switch an
# existing IdP you must delete + recreate with the same alias.
IDP_PROVIDER_ID="${IDP_PROVIDER_ID:-oidc}"
# Internal admin endpoint. In prod Keycloak serves under /auth – set
# KC_SERVER=http://localhost:8080/auth then (KC_HTTP_RELATIVE_PATH=/auth).
KC_SERVER="${KC_SERVER:-http://localhost:8080}"
KC_ADMIN="${KEYCLOAK_ADMIN:-admin}"
KC_ADMIN_PASSWORD="${KEYCLOAK_ADMIN_PASSWORD:-admin}"

# University OIDC client (required).
UNIV_CLIENT_ID="${UNIV_CLIENT_ID:?set UNIV_CLIENT_ID (the OIDC client id the university issued you)}"
UNIV_CLIENT_SECRET="${UNIV_CLIENT_SECRET:?set UNIV_CLIENT_SECRET}"
# Endpoints – either set UNIV_DISCOVERY_URL (autofills) or these directly.
UNIV_DISCOVERY_URL="${UNIV_DISCOVERY_URL:-}"
UNIV_AUTH_URL="${UNIV_AUTH_URL:-}"
UNIV_TOKEN_URL="${UNIV_TOKEN_URL:-}"
UNIV_JWKS_URL="${UNIV_JWKS_URL:-}"
UNIV_USERINFO_URL="${UNIV_USERINFO_URL:-}"
UNIV_ISSUER="${UNIV_ISSUER:-}"

KCADM=/opt/keycloak/bin/kcadm.sh
FBL_FLOW="first%20broker%20login"   # "first broker login", URL-encoded for the path

# kc: run kcadm in the container, output to host stdout.
kc()      { docker exec     "$KC_CONTAINER" "$KCADM" "$@"; }
# kc_pipe: same, but forwards host stdin (for `-f -` reads).
kc_pipe() { docker exec -i  "$KC_CONTAINER" "$KCADM" "$@"; }
jget()    { python3 -c "import sys,json;print(json.load(sys.stdin).get('$1',''))"; }

if ! docker ps --format '{{.Names}}' | grep -qx "$KC_CONTAINER"; then
  echo "✗ container '$KC_CONTAINER' is not running (set KC_CONTAINER)." >&2
  exit 1
fi

# ── 0. Resolve endpoints from the discovery doc if given ─────────────────────
if [ -n "$UNIV_DISCOVERY_URL" ]; then
  echo "→ Fetching OIDC discovery: $UNIV_DISCOVERY_URL"
  disco="$(curl -fsSL "$UNIV_DISCOVERY_URL")"
  UNIV_AUTH_URL="${UNIV_AUTH_URL:-$(printf '%s' "$disco" | jget authorization_endpoint)}"
  UNIV_TOKEN_URL="${UNIV_TOKEN_URL:-$(printf '%s' "$disco" | jget token_endpoint)}"
  UNIV_JWKS_URL="${UNIV_JWKS_URL:-$(printf '%s' "$disco" | jget jwks_uri)}"
  UNIV_USERINFO_URL="${UNIV_USERINFO_URL:-$(printf '%s' "$disco" | jget userinfo_endpoint)}"
  UNIV_ISSUER="${UNIV_ISSUER:-$(printf '%s' "$disco" | jget issuer)}"
fi
: "${UNIV_AUTH_URL:?could not resolve authorization endpoint – set UNIV_AUTH_URL or UNIV_DISCOVERY_URL}"
: "${UNIV_TOKEN_URL:?could not resolve token endpoint – set UNIV_TOKEN_URL or UNIV_DISCOVERY_URL}"

# ── 1. Authenticate kcadm (stored in the container for subsequent calls) ─────
echo "→ Authenticating kcadm against $KC_SERVER (realm master)"
kc config credentials --server "$KC_SERVER" --realm master \
  --user "$KC_ADMIN" --password "$KC_ADMIN_PASSWORD"

# ── 2. Create/update the university OIDC identity provider ───────────────────
idp_json() {
  UNIV_CLIENT_ID="$UNIV_CLIENT_ID" UNIV_CLIENT_SECRET="$UNIV_CLIENT_SECRET" \
  UNIV_AUTH_URL="$UNIV_AUTH_URL" UNIV_TOKEN_URL="$UNIV_TOKEN_URL" \
  UNIV_JWKS_URL="$UNIV_JWKS_URL" UNIV_USERINFO_URL="$UNIV_USERINFO_URL" \
  UNIV_ISSUER="$UNIV_ISSUER" IDP_ALIAS="$IDP_ALIAS" IDP_DISPLAY="$IDP_DISPLAY" \
  IDP_PROVIDER_ID="$IDP_PROVIDER_ID" \
  python3 - <<'PY'
import json, os
cfg = {
    "clientId": os.environ["UNIV_CLIENT_ID"],
    "clientSecret": os.environ["UNIV_CLIENT_SECRET"],
    "authorizationUrl": os.environ["UNIV_AUTH_URL"],
    "tokenUrl": os.environ["UNIV_TOKEN_URL"],
    "useJwksUrl": "true",
    "validateSignature": "true",
    "defaultScope": "openid profile email",
    "syncMode": "IMPORT",
    "pkceEnabled": "true",
    "pkceMethod": "S256",
    # WWU's token endpoint requires HTTP Basic client auth and rejects the
    # keycloak-oidc default (client_secret_post) with 401 invalid_client.
    "clientAuthMethod": "client_secret_basic",
    # WWU issues OPAQUE access tokens. Left on (the keycloak-oidc default),
    # Keycloak tries to parse the access token as a JWT and dies with
    # "Failed to parse JWT header" -> "Could not fetch attributes from userinfo
    # endpoint". Off => Keycloak treats it as opaque and reads identity from the
    # userinfo endpoint instead.
    "isAccessTokenJWT": "false",
}
for k, env in (("jwksUrl", "UNIV_JWKS_URL"), ("userInfoUrl", "UNIV_USERINFO_URL"),
               ("issuer", "UNIV_ISSUER")):
    if os.environ.get(env):
        cfg[k] = os.environ[env]
print(json.dumps({
    "alias": os.environ["IDP_ALIAS"],
    "displayName": os.environ["IDP_DISPLAY"],
    "providerId": os.environ["IDP_PROVIDER_ID"],
    "enabled": True,
    "trustEmail": True,        # skip the "verify your email" prompt
    "storeToken": False,
    "firstBrokerLoginFlowAlias": "first broker login",
    "config": cfg,
}))
PY
}

if kc get "identity-provider/instances/$IDP_ALIAS" -r "$REALM" >/dev/null 2>&1; then
  echo "→ Updating identity provider '$IDP_ALIAS'"
  idp_json | kc_pipe update "identity-provider/instances/$IDP_ALIAS" -r "$REALM" -f -
else
  echo "→ Creating identity provider '$IDP_ALIAS'"
  idp_json | kc_pipe create identity-provider/instances -r "$REALM" -f -
fi

# ── 3. Browser flow → Identity Provider Redirector → default = university ────
echo "→ Setting browser-flow default identity provider to '$IDP_ALIAS'"
browser_execs="$(kc get authentication/flows/browser/executions -r "$REALM")"
redir_exec_id="$(printf '%s' "$browser_execs" | python3 -c "
import sys,json
for e in json.load(sys.stdin):
    if e.get('providerId')=='identity-provider-redirector':
        print(e.get('id','')); break")"
redir_cfg_id="$(printf '%s' "$browser_execs" | python3 -c "
import sys,json
for e in json.load(sys.stdin):
    if e.get('providerId')=='identity-provider-redirector':
        print(e.get('authenticationConfig','')); break")"

if [ -z "$redir_exec_id" ]; then
  echo "  ! could not find the redirector execution in the browser flow – skipping." >&2
elif [ -n "$redir_cfg_id" ]; then
  kc update "authentication/config/$redir_cfg_id" -r "$REALM" \
    -s "config.defaultProvider=$IDP_ALIAS"
  echo "  updated existing redirector config"
else
  kc create "authentication/executions/$redir_exec_id/config" -r "$REALM" \
    -s "alias=${IDP_ALIAS}-idp-redirector" -s "config.defaultProvider=$IDP_ALIAS"
  echo "  created redirector config"
fi

# ── 4. First broker login → disable "Review Profile" ─────────────────────────
echo "→ Disabling 'Review Profile' in the first-broker-login flow"
review_exec_id="$(kc get "authentication/flows/$FBL_FLOW/executions" -r "$REALM" \
  | python3 -c "
import sys,json
for e in json.load(sys.stdin):
    if e.get('providerId')=='idp-review-profile':
        print(e.get('id','')); break")"
if [ -n "$review_exec_id" ]; then
  kc update "authentication/flows/$FBL_FLOW/executions" -r "$REALM" \
    -b "{\"id\":\"$review_exec_id\",\"requirement\":\"DISABLED\"}"
  echo "  Review Profile → DISABLED"
else
  echo "  ! 'Review Profile' execution not found – skipping." >&2
fi

echo "✓ Done. Set KEYCLOAK_IDP_HINT=$IDP_ALIAS on the web service and restart it."
