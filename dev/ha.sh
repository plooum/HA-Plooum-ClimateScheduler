#!/usr/bin/env bash
# Development Home Assistant for Plooum Climate Scheduler, with three fake thermostats.
#
#   dev/ha.sh start | stop | restart | status | logs [n] | reset
#
# HA listens on http://127.0.0.1:8132 (HA_PORT), config in dev/config, the integration linked from
# custom_components/. The Home Assistant venv is HA-Plooum-Cards' (HA_VENV), the token in dev/.ha-token.
set -euo pipefail
DEV="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$DEV")"
CONF="$DEV/config"
VENV="${HA_VENV:-$HOME/git/HA-Plooum-Cards/dev/.venv}"
PORT="${HA_PORT:-8132}"
LOG="$DEV/ha.log"
export HA_URL="http://127.0.0.1:$PORT"

alive() { [[ -f "$1" ]] && kill -0 "$(cat "$1")" 2>/dev/null; }

seed_http_store() {
  mkdir -p "$CONF/.storage"
  [[ -f "$CONF/.storage/http" ]] && return 0
  cat > "$CONF/.storage/http" <<JSON
{"version": 2, "minor_version": 2, "key": "http", "data": {"stable": {
  "server_host": ["127.0.0.1"], "server_port": $PORT, "cors_allowed_origins": [], "login_attempts_threshold": -1,
  "ip_ban_enabled": true, "ssl_profile": "modern", "use_x_frame_options": true,
  "created_at": "2026-01-01T00:00:00+00:00", "error": null, "error_message": null},
  "pending": null, "yaml_migration_done": true}}
JSON
}

cmd_start() {
  if alive "$DEV/.ha.pid"; then echo "HA already running — $HA_URL"; return 0; fi
  mkdir -p "$CONF/custom_components"
  ln -sfn "$ROOT/custom_components/plooum_climate_scheduler" "$CONF/custom_components/plooum_climate_scheduler"
  seed_http_store
  nohup "$VENV/bin/hass" -c "$CONF" --log-file "$LOG" >/dev/null 2>&1 &
  echo $! > "$DEV/.ha.pid"
  for _ in $(seq 120); do curl -sf -o /dev/null "$HA_URL/manifest.json" && break; sleep 1; done
  "$VENV/bin/python" "$DEV/bootstrap.py"
  echo "HA running — $HA_URL"
}

cmd_stop() {
  if alive "$DEV/.ha.pid"; then
    pid="$(cat "$DEV/.ha.pid")"; kill "$pid"
    for _ in $(seq 60); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
  fi
  rm -f "$DEV/.ha.pid"
  echo "stopped"
}

case "${1:-}" in
  start) cmd_start ;;
  stop) cmd_stop ;;
  restart) cmd_stop; cmd_start ;;
  status) alive "$DEV/.ha.pid" && echo "HA running (pid $(cat "$DEV/.ha.pid")) — $HA_URL" || echo "HA stopped" ;;
  logs) tail -n "${2:-50}" "$LOG" ;;
  reset) cmd_stop; git -C "$ROOT" clean -fdX "$CONF" >/dev/null; rm -f "$DEV/.ha-token"; echo "runtime state wiped" ;;
  *) echo "usage: $0 start|stop|restart|status|logs [n]|reset"; exit 2 ;;
esac
