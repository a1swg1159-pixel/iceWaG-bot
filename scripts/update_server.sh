#!/usr/bin/env bash
# Run directly on Linux, or send through scripts/update_server.ps1 over SSH.
set -Eeuo pipefail

stage="preflight"
trap 'result=$?; printf "\n[ERROR] Failed at: %s (exit %s).\n" "$stage" "$result" >&2; exit "$result"' ERR
fail() { printf '[ERROR] %s\n' "$*" >&2; exit 1; }

project_dir=${UPDATE_PROJECT_DIR:-"$HOME/iceWaG-bot"}
branch=${UPDATE_BRANCH:-main}
with_watchdog=${UPDATE_WITH_WATCHDOG:-0}
if [[ "$project_dir" == '~/'* ]]; then project_dir="$HOME/${project_dir:2}"; fi
cd -- "$project_dir" || fail "Project directory does not exist: $project_dir"
command -v git >/dev/null || fail "Git is not installed."
command -v docker >/dev/null || fail "Docker is not installed."
command -v flock >/dev/null || fail "flock (util-linux) is required to prevent concurrent updates."
[[ "$(cd -- "$(git rev-parse --show-toplevel)" && pwd -P)" == "$(pwd -P)" ]] || fail "RemotePath must be the Git repository root."
git check-ref-format --branch "$branch" >/dev/null
[[ "$(git branch --show-current)" == "$branch" ]] || fail "Server is not on branch $branch. No branch switch performed."
[[ -f .env ]] || fail "The server .env file is missing."

# Keep an update lock in Git metadata, not in the tracked source tree.
exec 9>"$(git rev-parse --git-path icewag-update.lock)"
flock -n 9 || fail "Another update is running in this repository."
docker compose version >/dev/null
docker info >/dev/null 2>&1 || fail "Cannot access Docker. Check that it is running and this SSH user has Docker permission."

compose=(docker compose)
services=(bot)
select_services() {
    compose=(docker compose)
    services=(bot)
    if [[ "$with_watchdog" == 1 ]]; then
        [[ -f docker-compose.alerts.yml ]] || fail "Watchdog files are missing from this server version."
        # Retain an explicitly configured Compose stack and ordinary overrides.
        if [[ -n "${COMPOSE_FILE:-}" ]]; then
            fail "WithWatchdog cannot be combined with COMPOSE_FILE. Use the server's custom Compose command."
        fi
        compose+=(-f docker-compose.yml)
        if [[ -f docker-compose.override.yml ]]; then
            compose+=(-f docker-compose.override.yml)
        elif [[ -f docker-compose.override.yaml ]]; then
            compose+=(-f docker-compose.override.yaml)
        fi
        compose+=(-f docker-compose.alerts.yml)
        services+=(watchdog)
    fi
}

stage="git pull"
before=$(git rev-parse --short HEAD)
printf '\n[1/4] Pulling origin/%s (current: %s)...\n' "$branch" "$before"
# Preserve server edits. Conflicts/divergence stop here; never reset or stash them.
GIT_TERMINAL_PROMPT=0 git -c merge.autostash=false -c rebase.autostash=false pull --ff-only --no-rebase origin "$branch" </dev/null
after=$(git rev-parse --short HEAD)

stage="compose validation"
select_services
"${compose[@]}" config --quiet </dev/null

stage="image build"
printf '\n[2/4] Building %s...\n' "${services[*]}"
# Build before replacing any running container; normal Docker layer cache applies.
"${compose[@]}" build "${services[@]}" </dev/null

stage="container replacement"
printf '\n[3/4] Starting %s...\n' "${services[*]}"
"${compose[@]}" up -d --no-build --no-deps "${services[@]}" </dev/null

stage="startup check"
printf '\n[4/4] Waiting up to 60 seconds for the Bot HTTP server...\n'
deadline=$((SECONDS + 60))
ready=0
while ((SECONDS < deadline)); do
    if "${compose[@]}" exec -T bot python -c '
import os
import urllib.error
import urllib.request
url = "http://127.0.0.1:" + os.getenv("PORT", "8081") + "/"
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    with opener.open(url, timeout=2):
        pass
except urllib.error.HTTPError as exc:
    if exc.code >= 500:
        raise
' </dev/null >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 2
done
"${compose[@]}" ps "${services[@]}" </dev/null
"${compose[@]}" logs --tail=60 --no-color "${services[@]}" </dev/null
[[ "$ready" == 1 ]] || fail "Bot HTTP server did not become ready. Check the logs above; containers were not automatically rolled back."

if [[ "$with_watchdog" == 1 ]]; then
    "${compose[@]}" ps --status running --services </dev/null | grep -Fxq watchdog || fail "Watchdog is not running. Check its SMTP configuration and logs."
fi
printf '\n[OK] Updated %s -> %s; Bot HTTP server is responding.\n' "$before" "$after"
printf 'Check QQ by mentioning the Bot with /ping in its enabled group. HTTP readiness alone does not confirm QQ is online.\n'
