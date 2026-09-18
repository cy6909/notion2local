#!/usr/bin/env bash
set -euo pipefail

data_root="${NOTION2LOCAL_DATA_ROOT:-/home/work_space/notion2local-data}"
app_uid="${NOTION2LOCAL_APP_UID:-10001}"
app_gid="${NOTION2LOCAL_APP_GID:-10001}"
min_free_gib="${NOTION2LOCAL_MIN_FREE_GIB:-10}"

if [[ "$data_root" == "/" || "$data_root" == /var/lib/docker* ]]; then
  echo "Refusing unsafe data root: $data_root" >&2
  exit 1
fi

install -d "$data_root"
mount_point="$(df -P "$data_root" | awk 'NR == 2 {print $6}')"
if [[ -z "$mount_point" || "$mount_point" == "/" ]]; then
  echo "Refusing data root on the root filesystem: $data_root" >&2
  echo "Set NOTION2LOCAL_DATA_ROOT to a path on the mounted data disk." >&2
  exit 1
fi

available_kib="$(df -Pk "$data_root" | awk 'NR == 2 {print $4}')"
minimum_kib=$((min_free_gib * 1024 * 1024))
if (( available_kib < minimum_kib )); then
  echo "Insufficient free space at $data_root: ${available_kib} KiB available" >&2
  exit 1
fi

install -d -m 0700 "$data_root/postgres"
install -d -m 0750 -o "$app_uid" -g "$app_gid" "$data_root/raw"
install -d -m 0750 -o "$app_uid" -g "$app_gid" "$data_root/blobs"
install -d -m 0700 -o "$app_uid" -g "$app_gid" "$data_root/config"
install -d -m 0750 "$data_root/projection"

echo "Notion2Local durable storage: $data_root"
df -hT "$data_root"
