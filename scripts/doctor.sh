#!/usr/bin/env bash
# Checks that this machine can build, run, test and deploy Relay. Prints what to install if not.
# Usage: ./scripts/doctor.sh      (or: make doctor)
set -uo pipefail
cd "$(dirname "$0")/.."

ok=0; warn=0; fail=0
green() { printf "  \033[32m✓\033[0m %-14s %s\n" "$1" "$2"; ok=$((ok + 1)); }
amber() { printf "  \033[33m!\033[0m %-14s %s\n" "$1" "$2"; warn=$((warn + 1)); }
red()   { printf "  \033[31m✗\033[0m %-14s %s\n" "$1" "$2"; fail=$((fail + 1)); }
have()  { command -v "$1" >/dev/null 2>&1; }
major() { printf '%s' "$1" | sed -E 's/^[^0-9]*([0-9]+).*/\1/'; }

echo "Relay doctor ($(uname -s) $(uname -m))"
echo
echo "Required"
if have git; then green git "$(git --version | awk '{print $3}')"; else red git "xcode-select --install"; fi

if have uv; then green uv "$(uv --version | awk '{print $2}')"; else red uv "brew install uv"; fi

if have uv && uv python find 3.12 >/dev/null 2>&1; then
  green python "$(uv python find 3.12) (managed by uv)"
else
  red python "uv python install 3.12"
fi

if have node; then
  v=$(node -v)
  if [ "$(major "$v")" -ge 22 ]; then green node "$v"; else red node "$v is too old: fnm install 24 && fnm default 24"; fi
else
  red node "brew install fnm, then: fnm install 24"
fi

if have pnpm; then
  v=$(pnpm -v)
  if [ "$(major "$v")" -ge 10 ]; then green pnpm "$v"; else red pnpm "$v is too old: npm install -g pnpm@10"; fi
else
  red pnpm "npm install -g pnpm@10"
fi

if have docker; then
  if docker info >/dev/null 2>&1; then
    green docker "$(docker --version | awk '{print $3}' | tr -d ,)"
  else
    red docker "installed but not running: open Docker Desktop (or OrbStack) and wait for it to start"
  fi
  if docker compose version >/dev/null 2>&1; then green compose "$(docker compose version --short 2>/dev/null)"; else red compose "Docker Compose v2 is missing (comes with Docker Desktop)"; fi
else
  red docker "brew install --cask docker   (or: brew install --cask orbstack)"
fi

if [ "$(uname -s)" = "Darwin" ]; then
  if have brew && [ -d "$(brew --prefix libomp 2>/dev/null)/lib" ]; then
    green libomp "$(brew --prefix libomp)"
  else
    red libomp "brew install libomp   (LightGBM needs it on macOS)"
  fi
fi

echo
echo "Recommended"
if have gh; then
  if gh auth status >/dev/null 2>&1; then green gh "signed in"; else amber gh "run: gh auth login"; fi
else
  amber gh "brew install gh"
fi
if have gcloud; then
  acct=$(gcloud config get-value account 2>/dev/null)
  proj=$(gcloud config get-value project 2>/dev/null)
  green gcloud "${acct:-no account} · project ${proj:-not set}"
else
  amber gcloud "brew install --cask gcloud-cli   (only needed to deploy)"
fi
if have psql; then green psql "$(psql --version | awk '{print $3}')"; else amber psql "brew install libpq && brew link --force libpq"; fi
if have jq; then green jq "$(jq --version)"; else amber jq "brew install jq"; fi
if have code; then green vscode "$(code --version 2>/dev/null | head -1)"; else amber vscode "VS Code: ⌘⇧P → Shell Command: Install 'code' command in PATH"; fi

echo
echo "This repo"
if [ -f .env ]; then green .env "present"; else amber .env "cp .env.example .env"; fi
if [ -d .venv ]; then green .venv "Python environment installed"; else amber .venv "make bootstrap"; fi
if [ -d node_modules ]; then green node_modules "installed"; else amber node_modules "make bootstrap"; fi
if [ -f packages/ml/models/service_min.txt ]; then
  green models "trained models present (the API uses LightGBM)"
else
  amber models "no trained models: the API uses the heuristic predictor (make train with data/private/)"
fi
n=$(find data/private -name '*.csv' 2>/dev/null | wc -l | tr -d ' ')
if [ "$n" -gt 0 ]; then green data/private "$n private CSVs (gitignored)"; else amber data/private "empty: only needed for make train / forecast / task2b"; fi
for port in 5173 8000 8080; do
  if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then amber "port $port" "in use (fine if it is Relay itself)"; fi
done

echo
printf "%s ok, %s warnings, %s problems\n" "$ok" "$warn" "$fail"
if [ "$fail" -gt 0 ]; then
  echo "Fix the ✗ lines (docs/SETUP_MAC.md has every step), then run this again."
  exit 1
fi
echo "Ready. Next: make bootstrap && make dev   (or: docker compose up --build)"
