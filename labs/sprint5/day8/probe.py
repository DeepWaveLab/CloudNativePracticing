import socket, sys, time
# usage: probe.py host port token seconds ; one UDP probe per second, logs ok/timeout with UTC time
host, port, token, secs = sys.argv[1], int(sys.argv[2]), sys.argv[3].encode(), int(sys.argv[4])
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(0.8)
end = time.time() + secs; i = 0
while time.time() < end:
    i += 1; t0 = time.time()
    s.sendto(f"seq{i}".encode() + token, (host, port))
    try:
        data, _ = s.recvfrom(1024); res = "ok " + data.decode().strip()
    except socket.timeout:
        res = "timeout"
    print(time.strftime("%H:%M:%S", time.gmtime(t0)), f"seq{i}", res, flush=True)
    time.sleep(max(0, 1 - (time.time() - t0)))
