#!/bin/bash
set -uo pipefail
CTX="${CTX:?Set CTX to the lab kubeconfig context}"
K="kubectl --context $CTX"
MANIFEST="$1"; N="${2:-10}"

for i in $(seq 1 "$N"); do
  $K -n wasmcloud delete workloaddeployment payload-component --ignore-not-found --wait=true >/dev/null 2>&1
  sleep 5
  t0=$(python3 -c 'import time;print(int(time.time()*1000))')
  $K apply -f "$MANIFEST" >/dev/null
  while :; do
    s=$($K -n wasmcloud get workloaddeployment payload-component \
        -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null)
    [ "$s" = "True" ] && break
    sleep 0.5
  done
  t1=$(python3 -c 'import time;print(int(time.time()*1000))')
  echo "wasmcloud_ready $((t1 - t0))"
done
