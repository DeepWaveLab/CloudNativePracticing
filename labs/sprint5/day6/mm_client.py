#!/usr/bin/env python3
import argparse
import asyncio
import base64
import json
import os
import time
import urllib.error
import urllib.request
import uuid

import websockets


def sanitize_message(message: dict) -> dict:
    copy = json.loads(json.dumps(message))
    if "matchmaker_matched" in copy and "token" in copy["matchmaker_matched"]:
        copy["matchmaker_matched"]["token"] = "<redacted>"
    return copy


def authenticate(http_url: str, server_key: str, device_id: str) -> tuple[str, str]:
    url = f"{http_url.rstrip('/')}/v2/account/authenticate/device?create=true"
    body = json.dumps({"id": device_id}).encode("utf-8")
    credentials = base64.b64encode(f"{server_key}:".encode("utf-8")).decode("ascii")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"device auth failed: http={exc.code} body={detail}") from exc

    token = payload["token"]
    account = get_account(http_url, token)
    return token, account["user"]["id"]


def get_account(http_url: str, token: str) -> dict:
    request = urllib.request.Request(
        f"{http_url.rstrip('/')}/v2/account",
        method="GET",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


async def run_client(name: str, ws_url: str, http_url: str, server_key: str, run_id: str) -> dict:
    device_id = f"sprint5-day6-{run_id}-{name}"
    token, user_id = authenticate(http_url, server_key, device_id)
    print(f"{name}: auth user_id={user_id} token=<redacted>", flush=True)

    notifications = []
    matched = None
    endpoint = None

    async with websockets.connect(f"{ws_url.rstrip('/')}?token={token}") as socket:
        await socket.send(json.dumps({
            "cid": name,
            "matchmaker_add": {
                "min_count": 2,
                "max_count": 2,
                "query": "*",
            },
        }))

        deadline = time.monotonic() + 45
        while time.monotonic() < deadline and (endpoint is None or matched is None):
            raw = await asyncio.wait_for(socket.recv(), timeout=max(1, deadline - time.monotonic()))
            message = json.loads(raw)
            print(f"{name}: ws {json.dumps(sanitize_message(message), sort_keys=True, ensure_ascii=False)}", flush=True)

            if "matchmaker_matched" in message:
                matched = message["matchmaker_matched"]

            for notification in message.get("notifications", {}).get("notifications", []):
                notifications.append(notification)
                content = notification.get("content", {})
                if isinstance(content, str):
                    content = json.loads(content)
                if notification.get("code") == 6006:
                    endpoint = content

    if endpoint is None:
        raise RuntimeError(f"{name}: did not receive allocation notification")
    if matched is None:
        raise RuntimeError(f"{name}: did not receive matchmaker_matched")

    return {
        "client": name,
        "user_id": user_id,
        "matched": matched,
        "allocation": endpoint,
        "notifications_count": len(notifications),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--http-url", default="http://127.0.0.1:7350")
    parser.add_argument("--ws-url", default="ws://127.0.0.1:7350/ws")
    parser.add_argument("--server-key", default=os.environ.get("NAKAMA_SERVER_KEY", "defaultkey"))
    parser.add_argument("--run-id", default=uuid.uuid4().hex[:10])
    args = parser.parse_args()

    results = await asyncio.gather(
        run_client("client-a", args.ws_url, args.http_url, args.server_key, args.run_id),
        run_client("client-b", args.ws_url, args.http_url, args.server_key, args.run_id),
    )
    endpoints = {result["allocation"]["endpoint"] for result in results}
    if len(endpoints) != 1:
        raise RuntimeError(f"clients received different endpoints: {sorted(endpoints)}")

    for result in results:
        if result["matched"] is not None and "token" in result["matched"]:
            result["matched"]["token"] = "<redacted>"

    print("SUMMARY " + json.dumps({
        "same_endpoint": True,
        "endpoint": next(iter(endpoints)),
        "results": results,
    }, sort_keys=True, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
