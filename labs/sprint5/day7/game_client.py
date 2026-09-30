#!/usr/bin/env python3
"""Sprint 5 Day 8 end-to-end player client, Python standard library only.

login (REST) -> matchmaker (realtime WebSocket) -> assignment (notification + RPC)
-> UDP through Quilkin with the routing token appended to each payload.
"""
import argparse
import base64
import json
import os
import socket
import struct
import threading
import time
import urllib.parse
import urllib.request
import uuid


def http_json(method, url, body=None, basic=None, bearer=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if basic is not None:
        req.add_header("Authorization", "Basic " + base64.b64encode(f"{basic}:".encode()).decode())
    if bearer is not None:
        req.add_header("Authorization", "Bearer " + bearer)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, json.loads(resp.read() or b"{}")


class WebSocket:
    """Minimal RFC 6455 client: text frames, client-side masking, ping/pong, no extensions."""

    def __init__(self, host, port, path):
        self.sock = socket.create_connection((host, port), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(1)
            if not chunk:
                raise ConnectionError("websocket handshake closed")
            head += chunk
        status_line = head.split(b"\r\n", 1)[0].decode()
        if " 101 " not in status_line:
            raise ConnectionError(f"websocket handshake failed: {status_line}")

    def _recv_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("websocket closed")
            buf += chunk
        return buf

    def _send_frame(self, opcode, payload):
        mask = os.urandom(4)
        header = bytes([0x80 | opcode])
        n = len(payload)
        if n < 126:
            header += bytes([0x80 | n])
        elif n < 65536:
            header += bytes([0x80 | 126]) + struct.pack("!H", n)
        else:
            header += bytes([0x80 | 127]) + struct.pack("!Q", n)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(header + mask + masked)

    def send_json(self, obj):
        self._send_frame(0x1, json.dumps(obj).encode())

    def recv_json(self, timeout):
        self.sock.settimeout(timeout)
        message = b""
        while True:
            b1, b2 = self._recv_exact(2)
            fin, opcode, n = b1 & 0x80, b1 & 0x0F, b2 & 0x7F
            if n == 126:
                n = struct.unpack("!H", self._recv_exact(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._recv_exact(8))[0]
            payload = self._recv_exact(n)
            if opcode == 0x9:
                self._send_frame(0xA, payload)
                continue
            if opcode == 0x8:
                raise ConnectionError("websocket closed by server")
            message += payload
            if fin:
                return json.loads(message)

    def close(self):
        try:
            self._send_frame(0x8, b"")
        finally:
            self.sock.close()


def udp_roundtrip(endpoint, payload, timeout):
    host, port = endpoint.rsplit(":", 1)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        s.sendto(payload, (host, int(port)))
        try:
            data, _ = s.recvfrom(2048)
            return data.decode(errors="replace").strip()
        except socket.timeout:
            return None


def run_player(name, args, results):
    log = lambda msg: print(f"{name}: {msg}", flush=True)
    api = f"http://{args.host}:{args.port}"

    status, session = http_json(
        "POST", f"{api}/v2/account/authenticate/device?create=true",
        {"id": f"sprint5-day8-{args.run_id}-{name}"}, basic=args.server_key)
    token = session["token"]
    log(f"login http={status} created={session.get('created')}")

    ws = WebSocket(args.host, args.port, "/ws?" + urllib.parse.urlencode({"token": token, "format": "json"}))
    ws.send_json({"cid": "mm", "matchmaker_add": {"min_count": 2, "max_count": 2, "query": "*"}})
    matched, assignment = None, None
    deadline = time.monotonic() + args.timeout
    while (matched is None or assignment is None) and time.monotonic() < deadline:
        msg = ws.recv_json(timeout=max(1, deadline - time.monotonic()))
        if "matchmaker_ticket" in msg:
            log("matchmaker ticket received")
        elif "matchmaker_matched" in msg:
            matched = msg["matchmaker_matched"]
            log(f"matchmaker_matched users={len(matched.get('users', []))}")
        elif "notifications" in msg:
            for n in msg["notifications"]["notifications"]:
                if n.get("code") == 6006:
                    assignment = json.loads(n["content"])
                    log(f"notification 6006 gameserver={assignment['gameserver']} proxy={assignment['proxy']}")
    ws.close()
    if matched is None or assignment is None:
        raise TimeoutError(f"{name}: matched={matched is not None} assignment={assignment is not None}")

    status, rpc = http_json("POST", f"{api}/v2/rpc/get_assignment", "{}", bearer=token)
    rpc_value = json.loads(rpc["payload"])
    log(f"rpc get_assignment http={status} found={rpc_value.get('found')} gameserver={rpc_value.get('gameserver')}")

    routing_token = assignment["routing_token"].encode()
    good = udp_roundtrip(assignment["proxy"], f"hello-{name}".encode() + routing_token, args.udp_timeout)
    log(f"udp via proxy with token -> {good!r}")
    bad = udp_roundtrip(assignment["proxy"], f"hello-{name}".encode() + b"zz0", args.udp_timeout)
    log(f"udp via proxy with wrong token -> {bad!r}")

    results[name] = {
        "gameserver": assignment["gameserver"],
        "proxy": assignment["proxy"],
        "routing_token": assignment["routing_token"],
        "rpc_matches_notification": all(rpc_value.get(k) == assignment[k] for k in ("gameserver", "proxy", "routing_token")),
        "udp_ack": good,
        "udp_wrong_token": bad,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7350)
    parser.add_argument("--server-key", default="defaultkey")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--udp-timeout", type=float, default=3)
    parser.add_argument("--run-id", default=uuid.uuid4().hex[:8])
    args = parser.parse_args()

    results, errors = {}, []

    def guarded(name):
        try:
            run_player(name, args, results)
        except Exception as exc:
            errors.append(f"{name}: {exc!r}")

    threads = [threading.Thread(target=guarded, args=(n,)) for n in ("player-a", "player-b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    a, b = results.get("player-a"), results.get("player-b")
    summary = {
        "errors": errors,
        "same_gameserver": bool(a and b and a["gameserver"] == b["gameserver"]),
        "same_token": bool(a and b and a["routing_token"] == b["routing_token"]),
        "rpc_matches": bool(a and b and a["rpc_matches_notification"] and b["rpc_matches_notification"]),
        "udp_ack_ok": bool(a and b and all(r["udp_ack"] and r["udp_ack"].startswith("ACK") for r in (a, b))),
        "wrong_token_blocked": bool(a and b and all(r["udp_wrong_token"] is None for r in (a, b))),
        "results": results,
    }
    print("SUMMARY " + json.dumps(summary, ensure_ascii=False), flush=True)
    raise SystemExit(0 if not errors and all(summary[k] for k in ("same_gameserver", "same_token", "rpc_matches", "udp_ack_ok", "wrong_token_blocked")) else 1)


if __name__ == "__main__":
    main()
