#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")/.."

if ! command -v npm >/dev/null 2>&1; then
  NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
  if [ -s "$NVM_DIR/nvm.sh" ]; then
    set +u
    . "$NVM_DIR/nvm.sh"
    if [ -f .nvmrc ]; then
      nvm use --silent >/dev/null
    else
      nvm use --silent default >/dev/null 2>&1 || true
    fi
    set -u
  fi
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "BayesMech Vision could not find npm. Install Node.js or configure NVM." >&2
  exit 127
fi

if [ -f ../.env ]; then
  set -a
  . ../.env
  set +a
fi
if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi
exec npm run start
