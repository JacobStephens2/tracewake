#!/usr/bin/env bash
# Create the local Host machine. Instance inventory and secrets stay with
# the operator; this only runs OpenTofu against deploy/tofu/local.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
MODULE="$ROOT/deploy/tofu/local"
CONF="${TRACEWAKE_CONF:-$HOME/.config/tracewake}"
TFVARS="$CONF/local.tfvars"
TFSTATE="$CONF/local.tfstate"
TFDATA="$CONF/local-tofu-data"

if ! command -v tofu >/dev/null 2>&1; then
  echo "tofu is required: https://opentofu.org/docs/intro/install/" >&2
  exit 1
fi
if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required and must be running." >&2
  exit 1
fi
if [[ ! -f "$TFVARS" ]]; then
  echo "Write $TFVARS with name, checkout, and http_port." >&2
  echo "See deploy/tofu/local/README.md." >&2
  exit 1
fi

mkdir -p "$CONF" "$TFDATA"
chmod 700 "$CONF"
export TF_DATA_DIR="$TFDATA"

(cd "$MODULE" && tofu init -input=false && tofu apply -input=false -auto-approve \
  -state="$TFSTATE" -var-file="$TFVARS")

echo "container_name=$(cd "$MODULE" && tofu output -raw -state="$TFSTATE" container_name)"
echo "http_url=$(cd "$MODULE" && tofu output -raw -state="$TFSTATE" http_url)"
echo "Apply deploy/ansible/host.yml against that container (community.docker.docker)."
