#!/bin/sh
set -eu
base=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if systemctl is-active --quiet aoa-v2.service 2>/dev/null; then
    exec systemctl stop aoa-v2.service
fi
exec "$base/python.sh" "$base/runtime/service.py" stop
