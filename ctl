#!/usr/bin/env bash
# Controls the NewsSharp systemd unit (ctl): start, restart or stop it.
#
#   ./ctl start|restart|stop
#
# The unit is the system unit installed from deploy/newssharp.service, so systemctl runs through sudo
# unless this script already runs as root. NEWSSHARP_SERVICE names the unit; a NEWSSHARP_SERVICE line
# in .env next to this script overrides it.

set -euo pipefail

NEWSSHARP_SERVICE="${NEWSSHARP_SERVICE:-newssharp}"

env_file="$(dirname "$0")/.env"
if [[ -f "$env_file" ]]; then
    line="$(grep -E '^[[:space:]]*(export[[:space:]]+)?NEWSSHARP_SERVICE=' "$env_file" | tail -n 1 || true)"
    if [[ -n "$line" ]]; then
        value="${line#*=}"
        value="${value%$'\r'}"
        value="${value%\"}"; value="${value#\"}"
        value="${value%\'}"; value="${value#\'}"
        [[ -n "$value" ]] && NEWSSHARP_SERVICE="$value"
    fi
fi

systemctl_cmd=(systemctl)
[[ "$(id -u)" -ne 0 ]] && systemctl_cmd=(sudo systemctl)

case "${1:-}" in
    start | restart | stop)
        "${systemctl_cmd[@]}" "$1" "$NEWSSHARP_SERVICE"
        "${systemctl_cmd[@]}" --no-pager --lines=0 status "$NEWSSHARP_SERVICE" || true
        ;;
    *)
        echo "usage: $(basename "$0") start|restart|stop" >&2
        exit 2
        ;;
esac
