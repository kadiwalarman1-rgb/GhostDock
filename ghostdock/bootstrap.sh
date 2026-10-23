#!/usr/bin/env bash
# Rendered by cloud.py. This runs on the new Amazon Linux 2023 instance.
set -euo pipefail

# Arm expiry before package installation. Best-effort; explicit cleanup is still required.
systemd-run --unit=ghostdock-expiry --on-active=@@TTL_SECONDS@@s /sbin/shutdown -h now
dnf install -y docker
systemctl enable --now docker

image=@@IMAGE@@
name=@@NAME@@
for attempt in 1 2 3; do
    if docker pull "$image"; then
        break
    fi
    if [ "$attempt" -eq 3 ]; then
        exit 1
    fi
    sleep 5
done

docker run --detach --name "$name" \
    --label io.ghostdock.managed=true \
    --label io.ghostdock.incident=@@INCIDENT@@ \
    --restart on-failure:3 \
    --read-only --cap-drop ALL --security-opt no-new-privileges \
    --user 10001:10001 --pids-limit 128 --memory 256m --cpus 0.5 \
    --tmpfs /tmp:rw,noexec,nosuid,size=16m \
    --publish 127.0.0.1:@@HOST_PORT@@:@@CONTAINER_PORT@@ "$image"
