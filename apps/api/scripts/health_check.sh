#!/bin/bash
set -uo pipefail

ALERT_EMAIL="luis@morehangouts.com"
LOG_FILE="/home/luis/bellas_xv/logs/health_check.log"
HOSTNAME=$(hostname)
ALERTS=()

mkdir -p "$(dirname "$LOG_FILE")"

DISK_USED=$(df / | awk 'NR==2 {print int($5)}')
if [ "$DISK_USED" -gt 80 ]; then
    ALERTS+=("Disk at ${DISK_USED}% on /")
fi

MEM_USED=$(free | awk '/^Mem:/ {printf "%d", ($3/$2)*100}')
if [ "$MEM_USED" -gt 85 ]; then
    ALERTS+=("Memory at ${MEM_USED}%")
fi

SWAP_TOTAL=$(free | awk '/^Swap:/ {print $2}')
SWAP_USED=$(free | awk '/^Swap:/ {print $3}')
if [ "$SWAP_TOTAL" -gt 0 ]; then
    SWAP_PCT=$((SWAP_USED * 100 / SWAP_TOTAL))
    if [ "$SWAP_PCT" -gt 50 ]; then
        ALERTS+=("Swap at ${SWAP_PCT}%")
    fi
fi

for svc in bellas-xv-api nginx postgresql fail2ban; do
    if ! systemctl is-active --quiet "$svc"; then
        ALERTS+=("Service down: $svc")
    fi
done

for domain in admin.shopbellasxv.com api.shopbellasxv.com; do
    DAYS_LEFT=$(echo | openssl s_client -servername "$domain" -connect "$domain":443 2>/dev/null \
        | openssl x509 -noout -enddate 2>/dev/null \
        | awk -F= '{print $2}' \
        | xargs -I{} date -d "{}" +%s 2>/dev/null \
        | awk -v now="$(date +%s)" '{print int(($1-now)/86400)}')
    if [ -n "$DAYS_LEFT" ] && [ "$DAYS_LEFT" -lt 14 ]; then
        ALERTS+=("SSL cert for $domain expires in $DAYS_LEFT days")
    fi
done

TS=$(date -Iseconds)
if [ ${#ALERTS[@]} -gt 0 ]; then
    BODY=$({
        echo "Bellas XV health alerts on $HOSTNAME at $TS:"
        echo
        printf '  - %s\n' "${ALERTS[@]}"
        echo
        echo "Disk:   $(df -h / | tail -1)"
        echo "Memory: $(free -h | grep Mem)"
        echo "Swap:   $(free -h | grep Swap)"
    })
    echo "$BODY" | mail -s "[Bellas XV] Health alert: ${#ALERTS[@]} issue(s)" "$ALERT_EMAIL"
    {
        echo "=== ALERT $TS (${#ALERTS[@]} issue(s)) ==="
        echo "$BODY"
        echo
    } >> "$LOG_FILE"
else
    echo "$TS ok" >> "$LOG_FILE"
fi

exit 0
