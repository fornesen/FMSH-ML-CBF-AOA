#!/bin/sh
set -eu
base=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
for unit in aoa-pl aoa-v2; do
    ln -sfn "$base/systemd/$unit.service" "/etc/systemd/system/$unit.service"
done
systemctl daemon-reload
systemctl enable aoa-pl.service aoa-v2.service
