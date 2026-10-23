#!/bin/bash
set -euo pipefail
PREFIX="$1"
CTX="${CTX:?Set CTX to the lab kubeconfig context}"
K=(kubectl --kubeconfig "${KUBECONFIG:?}" --context "$CTX")
POD=$("${K[@]}" -n wasmlab get pod -l app=node-shell -o jsonpath='{.items[0].metadata.name}')
NS() { "${K[@]}" -n wasmlab exec "$POD" -- nsenter -t 1 -m -- "$@"; }

NS cat /etc/containerd/config.toml            > "${PREFIX}-containerd-config.toml"
NS containerd config dump                     > "${PREFIX}-containerd-config-dump.toml"
"${K[@]}" get node -o jsonpath='{.items[0].status.runtimeHandlers}' | python3 -m json.tool \
                                              > "${PREFIX}-node-runtimehandlers.json"
NS sh -c 'ls -l /usr/bin/runc /usr/bin/crun /usr/bin/containerd-shim-runc-v2 \
  /usr/bin/containerd-shim-wasmedge-v1 /usr/bin/containerd-shim-wasmtime-v1 \
  /usr/bin/containerd-shim-spin-v2 /usr/bin/containerd-shim-spin-v1 \
  /usr/local/bin/containerd-shim-wasmedge-v1 /usr/local/bin/containerd-shim-spin-v2 \
  /usr/local/bin/crun 2>/dev/null || true' \
  | tr -s ' ' > "${PREFIX}-shim-binaries.txt"
NS sh -c 'for d in /etc/containerd /etc/containerd/conf.d /etc/containerd/config.toml.d \
  /opt/kwasm /opt/rcm /var/lib/rcm; do
    if [ -e "$d" ]; then echo "== $d"; ls -la "$d"; else echo "== $d MISSING"; fi; done' \
  | tr -s ' ' > "${PREFIX}-node-dirs.txt"
"${K[@]}" get runtimeclass -o custom-columns='NAME:.metadata.name,HANDLER:.handler' --no-headers \
  | sort | tr -s ' '                          > "${PREFIX}-runtimeclasses.txt"
"${K[@]}" get node -o jsonpath='{.items[0].metadata.labels}' | python3 -m json.tool \
                                              > "${PREFIX}-node-labels.json"
echo "Snapshot saved: ${PREFIX}-*"
