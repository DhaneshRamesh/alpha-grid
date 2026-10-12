#!/usr/bin/env bash
# Clean Ubuntu 24.04 setup for the provider-independent DATA_ONLY benchmarks.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source /etc/os-release
if [[ "${ID}" != ubuntu || "${VERSION_ID}" != 24.04 ]]; then
  echo "Ubuntu 24.04 is required" >&2
  exit 2
fi

sudo apt-get update
sudo apt-get install -y ca-certificates curl git build-essential pkg-config python3.12 python3.12-venv

if ! command -v rustup >/dev/null 2>&1; then
  installer="$(mktemp)"
  curl --fail --location --silent --show-error https://sh.rustup.rs -o "${installer}"
  sh "${installer}" -y --profile minimal --no-modify-path
  rm -f "${installer}"
fi
export PATH="${HOME}/.cargo/bin:${PATH}"
rustup toolchain install 1.99.0 --profile minimal --component clippy --component rustfmt

bootstrap="$(mktemp -d)"
trap 'rm -rf "${bootstrap}"' EXIT
python3.12 -m venv "${bootstrap}/venv"
"${bootstrap}/venv/bin/python" -m pip install uv==0.10.8
cd "${repo_root}"
"${bootstrap}/venv/bin/uv" sync --extra dev --locked --python python3.12

echo "Ready: source ${HOME}/.cargo/env, then run checks in rust/README.md"
