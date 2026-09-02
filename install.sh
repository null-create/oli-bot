#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/null-create/oli-bot.git"
APP_NAME="oli"      
INSTALL_DIR="${HOME}/.local/share/${APP_NAME}"
BRANCH="main"

echo "==> Installing ${APP_NAME}"

command_exists() { command -v "$1" >/dev/null 2>&1; }

if ! command_exists git; then
  echo "Error: git is required but not installed." >&2
  exit 1
fi

if ! command_exists python3; then
  echo "Error: python3 is required but not installed." >&2
  exit 1
fi

# Clone or update
if [ -d "${INSTALL_DIR}/.git" ]; then
  echo "==> Existing install found, updating..."
  git -C "${INSTALL_DIR}" fetch --depth 1 origin "${BRANCH}"
  git -C "${INSTALL_DIR}" reset --hard "origin/${BRANCH}"
else
  echo "==> Cloning ${REPO_URL}"
  rm -rf "${INSTALL_DIR}"
  git clone --depth 1 --branch "${BRANCH}" "${REPO_URL}" "${INSTALL_DIR}"
fi

cd "${INSTALL_DIR}"

# Prefer pipx (isolated, avoids dependency conflicts)
if command_exists pipx; then
  echo "==> Installing with pipx"
  pipx install --force "${INSTALL_DIR}"
else
  echo "==> pipx not found, installing pipx via pip --user"
  python3 -m pip install --user --upgrade pipx
  python3 -m pipx ensurepath
  python3 -m pipx install --force "${INSTALL_DIR}"
fi

echo ""
echo "==> Done! Restart your shell (or run 'source ~/.bashrc' / 'source ~/.zshrc')"
echo "==> Then run: ${APP_NAME} --help"