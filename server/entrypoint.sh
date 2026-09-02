#!/usr/bin/env bash
set -euo pipefail

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

# On Docker Desktop (Windows/macOS) the mount layer handles ownership and these
# values are ignored. On Linux they are what stops every clipped note from
# being owned by root.
if [ "$(id -u)" = "0" ]; then
  if ! getent group "$PGID" >/dev/null 2>&1; then
    groupadd -g "$PGID" clipper
  fi
  if ! getent passwd "$PUID" >/dev/null 2>&1; then
    useradd -u "$PUID" -g "$PGID" -M -s /usr/sbin/nologin clipper
  fi

  mkdir -p /vault /config
  chown -R "$PUID:$PGID" /srv 2>/dev/null || true

  if [ ! -w /vault ]; then
    echo "WARNING: /vault is not writable by UID $PUID." >&2
    echo "         Check the volume mount and PUID/PGID in your .env." >&2
  fi

  exec gosu "$PUID:$PGID" "$@"
fi

exec "$@"
