#!/bin/bash
set -uo pipefail
TAG="$1"
NSP=cnp5
W=ttl.sh/${TAG}-wasm:1h
C=ttl.sh/${TAG}-native-scratch:1h

pss_of() {   # sum PSS (KiB) of every process whose cmdline matches $1 and namespace cnp5
  local pat="$1" total=0 v
  for p in $(pgrep -f "$pat" 2>/dev/null); do
    grep -q "namespace.cnp5" /proc/"$p"/cmdline 2>/dev/null || \
      tr '\0' ' ' < /proc/"$p"/cmdline 2>/dev/null | grep -q "cnp5" || continue
    v=$(awk '/^Pss:/{print $2}' /proc/"$p"/smaps_rollup 2>/dev/null)
    total=$((total + ${v:-0}))
  done
  echo "$total"
}

for RT in wasmedge runc; do
  for N in 1 2 3; do
    for i in $(seq 1 "$N"); do
      if [ "$RT" = wasmedge ]; then
        ctr -n $NSP run -d --runtime io.containerd.wasmedge.v1 "$W" "s-$RT-$i" /payload.wasm stay 90 >/dev/null 2>&1
      else
        ctr -n $NSP run -d "$C" "s-$RT-$i" /payload stay 90 >/dev/null 2>&1
      fi
    done
    sleep 6
    SHIM=$(pss_of 'containerd-shim')
    CG=0
    for i in $(seq 1 "$N"); do
      v=$(cat /sys/fs/cgroup/$NSP/s-$RT-$i/memory.current 2>/dev/null || echo 0)
      CG=$((CG + v / 1024))
    done
    NP=$(pgrep -fc 'containerd-shim.*cnp5' 2>/dev/null || echo 0)
    echo "$RT n=$N shim_pss_KiB=$SHIM cgroup_total_KiB=$CG shim_procs=$NP"
    for i in $(seq 1 "$N"); do
      ctr -n $NSP task kill -s SIGKILL "s-$RT-$i" >/dev/null 2>&1
    done
    sleep 3
    for i in $(seq 1 "$N"); do ctr -n $NSP c rm "s-$RT-$i" >/dev/null 2>&1; done
    sleep 2
  done
done
