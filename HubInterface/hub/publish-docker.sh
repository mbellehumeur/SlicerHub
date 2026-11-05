#!/usr/bin/env bash
# Build and push Slicer Hub images to GitHub Container Registry.
#
#   ghcr.io/mbellehumeur/slicer-hub:slim|latest  — API + WS only
#   ghcr.io/mbellehumeur/slicer-hub:full         — includes SPA mounts
#
# Usage (from anywhere):
#   ./publish-docker.sh              # build+push slim and full
#   ./publish-docker.sh slim         # slim only
#   ./publish-docker.sh full         # full only (SPA trees must exist under hub/)
#   ./publish-docker.sh slim --no-push
#
# Prereqs:
#   - Docker Buildx
#   - gh auth login  OR  echo $GITHUB_TOKEN | docker login ghcr.io -u USER --password-stdin
#   - For :full — sync clients first:  python make_zip.py --skip-build
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HUB_IFACE="$(cd "$SCRIPT_DIR/.." && pwd)"
IMAGE="${IMAGE:-ghcr.io/mbellehumeur/slicer-hub}"
PLATFORMS="${PLATFORMS:-linux/amd64}"
PUSH=1
TARGET="all"

while [[ $# -gt 0 ]]; do
  case "$1" in
    slim|full|all)
      TARGET="$1"
      shift
      ;;
    --no-push)
      PUSH=0
      shift
      ;;
    --platform)
      PLATFORMS="$2"
      shift 2
      ;;
    -h|--help)
      sed -n '2,20p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown arg: $1" >&2
      exit 1
      ;;
  esac
done

GIT_SHA="$(git -C "$HUB_IFACE/.." rev-parse --short HEAD 2>/dev/null || echo local)"
EXTRA_TAGS=()
if [[ "$PUSH" -eq 1 ]]; then
  EXTRA_TAGS+=(--push)
else
  EXTRA_TAGS+=(--load)
fi

spa_ready() {
  local d
  for d in website worklist-client OHIF-client; do
    if [[ ! -f "$SCRIPT_DIR/$d/index.html" ]]; then
      echo "Missing hub/$d/index.html (needed for :full)" >&2
      return 1
    fi
  done
  return 0
}

stage_dockerignore() {
  local ignorefile="$1"
  STAGED_IGNORE="$HUB_IFACE/.dockerignore"
  IGNORE_BACKUP=""
  if [[ -f "$STAGED_IGNORE" ]]; then
    IGNORE_BACKUP="$(mktemp)"
    cp "$STAGED_IGNORE" "$IGNORE_BACKUP"
  fi
  cp "$HUB_IFACE/$ignorefile" "$STAGED_IGNORE"
}

restore_dockerignore() {
  if [[ -n "${IGNORE_BACKUP:-}" && -f "$IGNORE_BACKUP" ]]; then
    mv "$IGNORE_BACKUP" "$STAGED_IGNORE"
  else
    rm -f "${STAGED_IGNORE:-}"
  fi
  IGNORE_BACKUP=""
}

build_variant() {
  local variant="$1"
  local dockerfile ignorefile
  local -a tags=()

  case "$variant" in
    slim)
      dockerfile="hub/Dockerfile.slim"
      ignorefile="hub/.dockerignore.slim"
      tags+=(-t "${IMAGE}:slim" -t "${IMAGE}:latest" -t "${IMAGE}:slim-${GIT_SHA}")
      ;;
    full)
      dockerfile="hub/Dockerfile.fly"
      ignorefile="hub/.dockerignore.fly"
      tags+=(-t "${IMAGE}:full" -t "${IMAGE}:full-${GIT_SHA}")
      if ! spa_ready; then
        echo "Error: sync SPA mounts before building :full (python make_zip.py --skip-build)" >&2
        exit 1
      fi
      ;;
    *)
      echo "Unknown variant: $variant" >&2
      exit 1
      ;;
  esac

  stage_dockerignore "$ignorefile"
  trap restore_dockerignore EXIT

  echo "=== Building ${IMAGE} (${variant}) platforms=${PLATFORMS} ==="
  docker buildx build \
    --platform "$PLATFORMS" \
    -f "$HUB_IFACE/$dockerfile" \
    "${tags[@]}" \
    "${EXTRA_TAGS[@]}" \
    "$HUB_IFACE"

  trap - EXIT
  restore_dockerignore
}

cd "$HUB_IFACE"
if [[ ! -d python_client/src/hub_client ]]; then
  echo "Error: HubInterface/python_client missing" >&2
  exit 1
fi

case "$TARGET" in
  slim) build_variant slim ;;
  full) build_variant full ;;
  all)
    build_variant slim
    build_variant full
    ;;
esac

echo "Done. Image prefix: ${IMAGE}"
