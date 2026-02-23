#!/usr/bin/env bash
# push_to_ocir.sh — Build ARM64 image and push to OCI Container Registry
#
# Prerequisites:
#   - Docker Desktop with buildx enabled (or colima with arm64 support)
#   - OCI CLI configured: oci setup config
#   - docker login to OCIR:
#       oci artifacts container configuration get-namespace --compartment-id <cid>
#       docker login <region>.ocir.io -u '<namespace>/<username>' -p '<auth_token>'
#
# Usage:
#   ./scripts/push_to_ocir.sh
#   TAG=v2.6.0 ./scripts/push_to_ocir.sh

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
REGION="${OCI_REGION:-us-phoenix-1}"
NAMESPACE="${OCIR_NAMESPACE:-}"          # set via env or fill in here
REPO="oci-context-mcp"
TAG="${TAG:-latest}"

if [[ -z "$NAMESPACE" ]]; then
  echo "ERROR: OCIR_NAMESPACE is not set."
  echo "  Run: export OCIR_NAMESPACE=\$(oci artifacts container configuration get-namespace \\"
  echo "         --compartment-id \$OCI_COMPARTMENT_ID --query 'data.namespace' --raw-output)"
  exit 1
fi

IMAGE="${REGION}.ocir.io/${NAMESPACE}/${REPO}:${TAG}"

# ── Build ─────────────────────────────────────────────────────────────────────
echo "Building linux/arm64 image: $IMAGE"
docker buildx build \
  --platform linux/arm64 \
  --file "$(dirname "$0")/../Dockerfile" \
  --tag "$IMAGE" \
  --push \
  "$(dirname "$0")/.."

echo ""
echo "Pushed: $IMAGE"
echo ""
echo "Set in terraform.tfvars:"
echo "  ocir_image_url = \"$IMAGE\""
