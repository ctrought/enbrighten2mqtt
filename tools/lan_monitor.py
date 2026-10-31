#!/usr/bin/env python3
"""Watch a device's DP changes over the LAN (debug / protocol capture).

Reads device credentials from devices.json (never hard-coded). Usage:
    python tools/lan_monitor.py [devId]     # defaults to the first device
"""
import json
import sys
import time
from pathlib import Path

import tinytuya

devs = json.loads(Path("devices.json").read_text())
want = sys.argv[1] if len(sys.argv) > 1 else None
dcfg = next((x for x in devs if x["dev_id"] == want), devs[0]) if devs else None
if not dcfg:
    sys.exit("no devices in devices.json; run `python -m enbrighten2mqtt login`")

d = tinytuya.Device(dcfg["dev_id"], dcfg["ip"], dcfg["local_key"],
                    version=float(dcfg.get("version") or 3.5))
d.set_socketPersistent(True)
d.set_socketTimeout(6)
last: dict = {}


def log(dps, src):
    ch = {k: v for k, v in dps.items() if last.get(k) != v}
    if ch:
        last.update(dps)
        line = f"{time.strftime('%H:%M:%S')} {src} {json.dumps(ch)}"
        print(line, flush=True)
        with open("captures/lan_monitor.log", "a") as f:
            f.write(f"{time.time()} {src} {json.dumps(ch)}\n")


st = d.status()
if isinstance(st, dict) and "dps" in st:
    log(st["dps"], "init")
d.set_socketTimeout(1)
while True:
    try:
        m = d.receive()
        if isinstance(m, dict) and "dps" in m:
            log(m["dps"], "push")
    except Exception:
        pass
    try:
        st = d.status()
        if isinstance(st, dict) and "dps" in st:
            log(st["dps"], "poll")
    except Exception:
        pass
    time.sleep(1.5)
