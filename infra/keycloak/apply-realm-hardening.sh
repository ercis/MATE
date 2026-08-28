#!/usr/bin/env bash
# Re-apply the realm hardening settings that must survive a redeploy.
#
# The realm JSON (flows-funds-realm.json) is only imported into an EMPTY
# Keycloak DB (`start --import-realm`). On an existing VM that import is skipped,
# so edits to the JSON never reach the running realm. This one-shot service
# patches the handful of hardening attributes directly, idempotently, on every
# deploy — after Keycloak reports healthy.
#
# Scope is deliberately tiny: three realm attributes. It does NOT manage clients,
# roles, users, or identity providers, so it can never clobber the brokered WWU
# IdP (configure-university-idp.sh) or any other admin-console change. Re-running
# with the same values is a no-op.
#
# Safe failure: `set -e` makes the container exit non-zero on any kcadm error;
# Keycloak and the app keep running, the settings just stay unpatched. Check
# this container's logs after a deploy to confirm the success line prints.
set -euo pipefail

KCADM=/opt/keycloak/bin/kcadm.sh
# Internal address of the Keycloak service. The /auth suffix matches
# KC_HTTP_RELATIVE_PATH in the prod overlay — without it every kcadm call 404s.
SERVER="${KC_APPLY_SERVER:-http://keycloak:8080/auth}"
REALM=flows-funds

"$KCADM" config credentials \
  --server "$SERVER" \
  --realm master \
  --user "$KEYCLOAK_ADMIN" \
  --password "$KEYCLOAK_ADMIN_PASSWORD"

"$KCADM" update "realms/$REALM" \
  -s sslRequired=EXTERNAL \
  -s ssoSessionIdleTimeout=28800 \
  -s ssoSessionMaxLifespan=86400

echo "realm '$REALM' hardening applied: sslRequired=EXTERNAL ssoSessionIdleTimeout=28800 ssoSessionMaxLifespan=86400"
