#!/usr/bin/env bash
# Set up or reconfigure groundtruth on Azure Container Apps. Safe to re-run:
# creates what's missing, updates what exists.
#
#   deploy/deploy.sh          use the image built for the current commit (HEAD)
#   deploy/deploy.sh <sha>    use the image built for another commit (e.g. to roll back)
#
# Run it for the first deploy and whenever secrets or settings change. Routine
# code deploys happen in CI (.github/workflows/ci-cd.yml), which only swaps images.
# Guide: docs/deploy-azure.md
#
# Needs: az CLI logged in (az login), deploy/prod.env, the repo's .venv, and the
# image already built by the ci-cd workflow.
set -euo pipefail
cd "$(dirname "$0")/.."

ENV_FILE=deploy/prod.env
PY=.venv/bin/python
step() { printf '\n==> %s\n' "$*"; }
die() { printf '!! %s\n' "$*" >&2; exit 1; }

[[ -f $ENV_FILE ]] || die "Missing $ENV_FILE. Copy deploy/prod.env.example and fill it in."
[[ -x $PY ]] || die "Missing .venv (python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt)."
command -v az >/dev/null || die "Azure CLI not found: curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash"

# Load prod.env through python-dotenv: values like LLM_EXTRA_BODY hold JSON that
# plain `source` would mangle. Exported, so they override .env for the helper scripts.
eval "$("$PY" - "$ENV_FILE" <<'EOF'
import shlex, sys
from dotenv import dotenv_values
for key, value in dotenv_values(sys.argv[1]).items():
    print(f"export {key}={shlex.quote(value or '')}")
EOF
)"

for key in AZ_LOCATION AZ_RESOURCE_GROUP AZ_ENVIRONMENT AZ_APP AZ_JOB AZ_LOG_WORKSPACE SOURCE_CRON \
           GHCR_USERNAME GHCR_TOKEN LLM_API_BASE LLM_MODEL LLM_API_KEY MONGODB_URI \
           TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID TELEGRAM_WEBHOOK_SECRET JOB_SECRET; do
  [[ -n "${!key:-}" ]] || die "$key is empty in $ENV_FILE"
done

TAG="${1:-$(git rev-parse HEAD)}"
REPO="${GHCR_USERNAME,,}/groundtruth"
IMAGE="ghcr.io/$REPO:$TAG"
RG="$AZ_RESOURCE_GROUP"

step "Checking the image exists: $IMAGE"
token=$(curl -fsS -u "$GHCR_USERNAME:$GHCR_TOKEN" "https://ghcr.io/token?scope=repository:$REPO:pull" \
        | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["token"])') \
  || die "GitHub registry login failed; check GHCR_USERNAME / GHCR_TOKEN (needs read:packages)."
curl -fsS -o /dev/null -H "Authorization: Bearer $token" \
     -H "Accept: application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.v2+json" \
     "https://ghcr.io/v2/$REPO/manifests/$TAG" \
  || die "No image tagged $TAG yet. Push to main (or run the ci-cd workflow) and wait for its build job."

step "Azure login and providers"
az account show --query "{subscription:name, user:user.name}" -o tsv || die "Not logged in: run az login"
az extension add --name containerapp --upgrade -o none --only-show-errors
az provider register -n Microsoft.App --wait -o none
az provider register -n Microsoft.OperationalInsights --wait -o none

step "Resource group $RG ($AZ_LOCATION)"
az group create -n "$RG" -l "$AZ_LOCATION" -o none

step "Log workspace $AZ_LOG_WORKSPACE (0.1 GB/day cap, 30-day retention)"
if ! az monitor log-analytics workspace show -g "$RG" -n "$AZ_LOG_WORKSPACE" -o none 2>/dev/null; then
  az monitor log-analytics workspace create -g "$RG" -n "$AZ_LOG_WORKSPACE" -l "$AZ_LOCATION" \
    --quota 0.1 --retention-time 30 -o none
fi

step "Container Apps environment $AZ_ENVIRONMENT"
if ! az containerapp env show -g "$RG" -n "$AZ_ENVIRONMENT" -o none 2>/dev/null; then
  ws_id=$(az monitor log-analytics workspace show -g "$RG" -n "$AZ_LOG_WORKSPACE" --query customerId -o tsv)
  ws_key=$(az monitor log-analytics workspace get-shared-keys -g "$RG" -n "$AZ_LOG_WORKSPACE" --query primarySharedKey -o tsv)
  az containerapp env create -g "$RG" -n "$AZ_ENVIRONMENT" -l "$AZ_LOCATION" \
    --logs-workspace-id "$ws_id" --logs-workspace-key "$ws_key" -o none
fi

step "Database indexes (Atlas)"
"$PY" -m scripts.init_db

# Sensitive values become Container Apps secrets, referenced by env vars; the rest
# are plain env vars. Secret names: lowercase, dashes.
SECRET_KEYS=(LLM_API_KEY MONGODB_URI TELEGRAM_BOT_TOKEN TELEGRAM_WEBHOOK_SECRET JOB_SECRET)
PLAIN_KEYS=(LLM_API_BASE LLM_MODEL LLM_EXTRA_BODY LLM_MAX_TOKENS LLM_TIMEOUT_SECONDS LLM_MAX_RETRIES
            MONGODB_DB_NAME TELEGRAM_CHAT_ID STORIES_PER_RUN SOURCE_INTERVAL_HOURS)
SECRETS=(); ENVS=()
for key in "${SECRET_KEYS[@]}"; do
  name=$(tr 'A-Z_' 'a-z-' <<<"$key")
  SECRETS+=("$name=${!key}")
  ENVS+=("$key=secretref:$name")
done
for key in "${PLAIN_KEYS[@]}"; do
  [[ -n "${!key:-}" ]] && ENVS+=("$key=${!key}")
done
REGISTRY=(--registry-server ghcr.io --registry-username "$GHCR_USERNAME" --registry-password "$GHCR_TOKEN")
SIZE=(--cpu 0.25 --memory 0.5Gi)
# Unique per deploy, so a redeploy with only secret changes still rolls a new revision.
REVISION="d$(date -u +%Y%m%d%H%M%S)"

step "App $AZ_APP -> $IMAGE"
if az containerapp show -g "$RG" -n "$AZ_APP" -o none 2>/dev/null; then
  az containerapp registry set -g "$RG" -n "$AZ_APP" --server ghcr.io \
    --username "$GHCR_USERNAME" --password "$GHCR_TOKEN" -o none
  az containerapp secret set -g "$RG" -n "$AZ_APP" --secrets "${SECRETS[@]}" -o none
  az containerapp update -g "$RG" -n "$AZ_APP" --image "$IMAGE" --set-env-vars "${ENVS[@]}" \
    --min-replicas 0 --max-replicas 1 "${SIZE[@]}" --revision-suffix "$REVISION" -o none
else
  az containerapp create -g "$RG" -n "$AZ_APP" --environment "$AZ_ENVIRONMENT" \
    --image "$IMAGE" "${REGISTRY[@]}" \
    --ingress external --target-port 8080 \
    --min-replicas 0 --max-replicas 1 "${SIZE[@]}" \
    --scale-rule-name http --scale-rule-type http --scale-rule-http-concurrency 10 \
    --secrets "${SECRETS[@]}" --env-vars "${ENVS[@]}" \
    --revision-suffix "$REVISION" -o none
fi
FQDN=$(az containerapp show -g "$RG" -n "$AZ_APP" --query properties.configuration.ingress.fqdn -o tsv)
URL="https://$FQDN"

step "Scheduled job $AZ_JOB ($SOURCE_CRON UTC)"
JOB_COMMAND=(--command python -m scripts.run_source)
if az containerapp job show -g "$RG" -n "$AZ_JOB" -o none 2>/dev/null; then
  az containerapp job registry set -g "$RG" -n "$AZ_JOB" --server ghcr.io \
    --username "$GHCR_USERNAME" --password "$GHCR_TOKEN" -o none
  az containerapp job secret set -g "$RG" -n "$AZ_JOB" --secrets "${SECRETS[@]}" -o none
  az containerapp job update -g "$RG" -n "$AZ_JOB" --image "$IMAGE" --set-env-vars "${ENVS[@]}" \
    --cron-expression "$SOURCE_CRON" "${JOB_COMMAND[@]}" -o none
else
  az containerapp job create -g "$RG" -n "$AZ_JOB" --environment "$AZ_ENVIRONMENT" \
    --trigger-type Schedule --cron-expression "$SOURCE_CRON" \
    --replica-timeout 300 --replica-retry-limit 1 --parallelism 1 --replica-completion-count 1 \
    --image "$IMAGE" "${REGISTRY[@]}" "${SIZE[@]}" "${JOB_COMMAND[@]}" \
    --secrets "${SECRETS[@]}" --env-vars "${ENVS[@]}" -o none
fi

step "Health check $URL/healthz (first request wakes the app from zero)"
curl -fsS --retry 6 --retry-delay 5 --retry-all-errors --max-time 60 "$URL/healthz" && echo

step "Telegram webhook"
"$PY" -m scripts.set_webhook "$URL"

cat <<EOF

Deployed $IMAGE
  App:  $URL
  Job:  $AZ_JOB, $SOURCE_CRON UTC

Next:
  Send a pick list now (ignores the 44h gap):
    curl -X POST "$URL/jobs/source?force=true" -H "X-Job-Secret: \$JOB_SECRET"
  Run the scheduled job by hand (respects the gap):
    az containerapp job start -g $RG -n $AZ_JOB
  Logs:
    az containerapp logs show -g $RG -n $AZ_APP --tail 50
EOF
