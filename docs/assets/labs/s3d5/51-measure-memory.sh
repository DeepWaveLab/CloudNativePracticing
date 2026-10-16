#!/bin/bash
set -uo pipefail
TAG="$1"
NSP=cnp5
W=ttl.sh/${TAG}-wasm:1h
C=ttl.sh/${TAG}-native-scratch:1h

echo "=== shim processes BEFORE anything is running"
ps -eo rss,comm --sort=-rss | grep -E 'containerd-shim|COMMAND' | head -10 || true

report() {
  local id="$1" label="$2"
  local pid cg path
  pid=$(ctr -n $NSP task ls | awk -v i="$id" '$1==i{print $2}')
  cg=$(cat /proc/"$pid"/cgroup | head -1 | cut -d: -f3)
  path=/sys/fs/cgroup${cg}
  echo "--- $label  container=$id  pid=$pid"
  echo "    cgroup            = $cg"
  echo "    memory.current    = $(cat "$path"/memory.current) bytes"
  echo "    memory.peak       = $(cat "$path"/memory.peak 2>/dev/null || echo n/a) bytes"
  echo "    anon              = $(awk '$1=="anon"{print $2}' "$path"/memory.stat) bytes"
  echo "    file              = $(awk '$1=="file"{print $2}' "$path"/memory.stat) bytes"
  echo "    procs in cgroup   = $(wc -l < "$path"/cgroup.procs)"
  ps -o pid,rss,comm -p "$pid"
}

ctr -n $NSP run -d --runtime io.containerd.wasmedge.v1 "$W" memw /payload.wasm stay 120
ctr -n $NSP run -d "$C" memc /payload stay 120
sleep 5

report memw wasmedge
report memc runc

echo "=== all shim processes WHILE both are running (rss in KiB)"
ps -eo pid,rss,args --sort=-rss | grep -E 'containerd-shim' | grep -v grep

echo "=== shim binary sizes on disk"
ls -l /usr/bin/containerd-shim-runc-v2 /usr/local/bin/containerd-shim-wasmedge-v1

ctr -n $NSP task kill -s SIGKILL memw 2>/dev/null
ctr -n $NSP task kill -s SIGKILL memc 2>/dev/null
sleep 3
ctr -n $NSP c rm memw memc 2>/dev/null
echo "=== cleaned up"
