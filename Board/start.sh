#!/bin/sh
set -eu
base=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if systemctl is-enabled --quiet aoa-v2.service 2>/dev/null; then
    exec systemctl start aoa-v2.service
fi
exec "$base/python.sh" "$base/runtime/check_runtime.py" start
