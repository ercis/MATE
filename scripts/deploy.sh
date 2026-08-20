#!/usr/bin/env bash
#
# Local push-to-deploy for the uni VM (pm-mate.uni-muenster.de).
#
# Run this from your laptop **while connected to the department dev VPN** – the
# VM's SSH port is only reachable through the VPN. It:
#   1. pushes the current branch to GitHub,
#   2. SSHes into the VM, updates the deploy clone to match origin, and
#      rebuilds + restarts the stack,
#   3. health-checks the public URL.
#
# Usage:
#   ./scripts/deploy.sh              # push current branch, then deploy
#   ./scripts/deploy.sh --no-push    # skip the push, just redeploy origin's state
#
# The VM's hostname, SSH port and login are NOT in this repo - it is public.
# Put them in scripts/deploy.env (gitignored; copy scripts/deploy.env.example),
# or export them yourself:
#   DEPLOY_HOST  DEPLOY_PORT  DEPLOY_USER  DEPLOY_DIR  DEPLOY_BRANCH
#
# Tip: run `ssh-copy-id -p "$DEPLOY_PORT" "$DEPLOY_USER@$DEPLOY_HOST"` once so
# you don't get a password prompt on every deploy.

set -euo pipefail

# Optional local config, kept out of git.
ENV_FILE="$(dirname "${BASH_SOURCE[0]}")/deploy.env"
# shellcheck source=/dev/null
[[ -f "$ENV_FILE" ]] && source "$ENV_FILE"

missing=()
[[ -n "${DEPLOY_HOST:-}" ]] || missing+=(DEPLOY_HOST)
[[ -n "${DEPLOY_USER:-}" ]] || missing+=(DEPLOY_USER)
if (( ${#missing[@]} )); then
  echo "✗ Missing: ${missing[*]}" >&2
  echo "  Set them in scripts/deploy.env (see scripts/deploy.env.example) or" >&2
  echo "  export them. The real values are in the team's deployment notes." >&2
  exit 2
fi

SSH_HOST="$DEPLOY_HOST"
SSH_PORT="${DEPLOY_PORT:-22}"
SSH_USER="$DEPLOY_USER"
REMOTE_DIR="${DEPLOY_DIR:-~/mate}"
BRANCH="${DEPLOY_BRANCH:-$(git rev-parse --abbrev-ref HEAD)}"
PUBLIC_URL="${DEPLOY_PUBLIC_URL:-https://pm-mate.uni-muenster.de}"

push=1
[[ "${1:-}" == "--no-push" ]] && push=0

if [[ "$push" == "1" ]]; then
  echo "▶ Pushing '$BRANCH' to origin…"
  git push origin "$BRANCH"
fi

echo "▶ Deploying on $SSH_USER@$SSH_HOST ($REMOTE_DIR)…"
# Unquoted heredoc: $REMOTE_DIR / $BRANCH are expanded locally and baked into
# the script the VM runs. The remote part has no other shell expansions.
ssh -p "$SSH_PORT" "$SSH_USER@$SSH_HOST" bash -s <<EOF
set -euo pipefail
cd $REMOTE_DIR

echo "  • fetching origin/$BRANCH"
git fetch --quiet origin "$BRANCH"
# Mirror origin exactly. Local secrets live in the gitignored .env, which is
# untracked and therefore untouched by reset.
git reset --hard "origin/$BRANCH"

echo "  • rebuilding + restarting the stack"
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build

echo "  • pruning dangling images"
docker image prune -f >/dev/null
EOF

echo "▶ Waiting for $PUBLIC_URL/health …"
for _ in $(seq 1 36); do
  if curl -fsS -o /dev/null "$PUBLIC_URL/health"; then
    echo "✔ Deployed – $PUBLIC_URL is healthy."
    exit 0
  fi
  sleep 5
done

echo "⚠ Deploy ran, but /health didn't come up within ~3 min."
echo "  Check logs: ssh -p $SSH_PORT $SSH_USER@$SSH_HOST 'cd $REMOTE_DIR && docker compose logs --tail=80 api web'"
exit 1
