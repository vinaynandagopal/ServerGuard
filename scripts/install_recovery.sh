#!/bin/bash
# Installs the ServerGuard recovery helper.
# Usage: sudo ./scripts/install_recovery.sh [service ...]
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run with sudo:  sudo $0 [service ...]" >&2
    exit 1
fi
APP_USER="${SUDO_USER:-}"
if [[ -z "$APP_USER" || "$APP_USER" == "root" ]]; then
    echo "Run it through sudo from the normal user that runs ServerGuard." >&2
    exit 1
fi
if [[ ! "$APP_USER" =~ ^[a-z_][a-z0-9_-]*$ ]]; then
    echo "Unexpected user name: $APP_USER" >&2
    exit 1
fi

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELPER=/usr/local/sbin/serverguard-restart
CONF_DIR=/etc/serverguard
ALLOWLIST="$CONF_DIR/allowed-services"
SUDOERS=/etc/sudoers.d/serverguard
NAME_RE='^[A-Za-z0-9][A-Za-z0-9@_.:-]{0,99}$'

# Root-owned copy: the user cannot modify what sudo is allowed to run.
install -m 0755 -o root -g root "$SRC_DIR/serverguard-restart" "$HELPER"

install -d -m 0755 -o root -g root "$CONF_DIR"
if [[ ! -f "$ALLOWLIST" ]]; then
    echo "# Services ServerGuard may restart. One exact service name per line." > "$ALLOWLIST"
fi
for svc in "$@"; do
    if [[ ! "$svc" =~ $NAME_RE ]]; then
        echo "Skipping invalid service name: $svc" >&2
        continue
    fi
    grep -qxF -- "$svc" "$ALLOWLIST" || echo "$svc" >> "$ALLOWLIST"
done
chown root:root "$ALLOWLIST"
chmod 0644 "$ALLOWLIST"   # readable by the app, writable only by root

# sudoers: this user may run ONLY the helper, as root, without a password.
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
echo "$APP_USER ALL=(root) NOPASSWD: $HELPER *" > "$TMP"
visudo -cf "$TMP" > /dev/null          # refuse to install a broken sudoers file
install -m 0440 -o root -g root "$TMP" "$SUDOERS"
visudo -c > /dev/null

echo "Installed helper : $HELPER"
echo "Allowlist        : $ALLOWLIST"
echo "Sudoers rule     : $SUDOERS  (user: $APP_USER)"
echo "Allowed services :"
grep -v '^#' "$ALLOWLIST" | sed 's/^/  - /'
