#!/usr/bin/env python3
"""通过网关取 bot 所在的服务器列表（REST 的 /users/@me/guilds 对 bot 返回 403）。"""
import json, os, sys, time, urllib.request
from websocket import create_connection

tok = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
if not tok:
    sys.exit("缺 DISCORD_BOT_TOKEN")

ws = create_connection("wss://gateway.discord.gg/?v=10&encoding=json", timeout=20)
hello = json.loads(ws.recv())
interval = hello["d"]["heartbeat_interval"] / 1000
ws.send(json.dumps({"op": 1, "d": None}))
ws.send(json.dumps({"op": 2, "d": {"token": f"Bot {tok}", "intents": 1,
                                   "properties": {"os": "mac", "browser": "research", "device": "local"}}}))
deadline = time.time() + 25
guilds = None
while time.time() < deadline:
    try:
        msg = json.loads(ws.recv())
    except Exception:
        break
    if msg.get("op") == 11:
        continue
    if msg.get("t") == "READY":
        guilds = msg["d"]["guilds"]
        print("bot:", msg["d"]["user"]["username"])
        break
ws.close()
if guilds is None:
    print("没拿到 READY（可能 token 无效或被限）")
    sys.exit(1)
print(f"服务器 {len(guilds)} 个")
for g in guilds:
    print(f"  {g['id']}  {g.get('name','')}")
