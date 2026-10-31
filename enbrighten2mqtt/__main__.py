"""enbrighten2mqtt CLI.

Commands:
  extract-keys <apk>       Pull Tuya OEM credentials from the Enbrighten APK.
  login                    Log into the Enbrighten account; save local keys.
  list                     Show saved devices.
  capture --device <id>    Interactive DP capture harness.
  probe --ip <ip>          Try to reach a device locally and dump its status.
  discover                 Scan the LAN for devices and show their IPs.
  serve                    Run the MQTT bridge.
"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import sys
from pathlib import Path


def _load_json(path: str):
    p = Path(path)
    if not p.exists():
        sys.exit(f"missing {path}; run the prerequisite step first")
    return json.loads(p.read_text())


def cmd_extract_keys(args):
    from .extract_keys import extract
    creds = extract(args.apk, args.out)
    print(json.dumps(creds.__dict__, indent=2))
    print(f"\nWrote {args.out}" + ("" if creds.is_complete()
          else "  (INCOMPLETE - see notes)"))


def cmd_login(args):
    from .cloud.tuya_oem import TuyaOemApi, save_devices
    creds = _load_json(args.creds)
    email = args.email or input("Enbrighten account email: ")
    password = getpass.getpass("Enbrighten password: ")
    api = TuyaOemApi(creds)
    api.login(email, password)
    devices = api.devices()
    save_devices(devices, args.out)
    # Best-effort per-product DP schema (generic device support); non-fatal.
    try:
        schemas = api.schema()
    except Exception as e:  # noqa: BLE001
        schemas, _ = {}, print(f"  product schema fetch skipped: {e}")
    if schemas:
        Path("schema").mkdir(exist_ok=True)
        for pid, sch in schemas.items():
            Path(f"schema/{pid}.json").write_text(json.dumps(sch, indent=2))
        print(f"Saved {len(schemas)} product schema(s) to schema/")
    print(f"Saved {len(devices)} device(s) to {args.out}")
    for d in devices:
        print(f"  {d.name:24} {d.dev_id}  key={'*' * len(d.local_key)}  "
              f"{d.category}/{d.product_id}  ip={d.ip}  online={d.online}")


def cmd_list(args):
    for d in _load_json(args.devices):
        print(f"{d['name']:24} {d['dev_id']}  {d.get('category')}/"
              f"{d.get('product_id')}  ip={d.get('ip')}")


def cmd_probe(args):
    from .local.device import DeviceInfo, detect_version, LocalDevice
    info = DeviceInfo(args.device_id, args.ip, args.local_key,
                      float(args.version) if args.version else None)
    v = info.version or detect_version(info)
    if not v:
        sys.exit("could not reach device on any protocol version")
    info.version = v
    dev = LocalDevice(info)
    dev.connect()
    print(f"protocol {v}")
    print(json.dumps(dev.status(), indent=2))


def cmd_capture(args):
    from .capture import CaptureSession
    from .local.device import DeviceInfo
    devices = {d["dev_id"]: d for d in _load_json(args.devices)}
    d = devices.get(args.device) or next(iter(devices.values()))
    info = DeviceInfo(d["dev_id"], args.ip or d.get("ip") or "",
                      d["local_key"], _ver(d.get("version")))
    if not info.ip:
        sys.exit("no IP known for device; pass --ip")
    CaptureSession(info, args.out).run()


def cmd_discover(args):
    from .local.discovery import scan_lan
    known = {d["dev_id"]: d for d in _load_json(args.devices)}
    if not known:
        sys.exit(f"no devices in {args.devices}; run `login` first")
    found = scan_lan(known.keys())
    print(f"Found {len(found)}/{len(known)} device(s) on the LAN:")
    for dev_id, d in sorted(known.items(), key=lambda kv: kv[1].get("name", "")):
        info = found.get(dev_id)
        where = (f"{info['ip']:16} proto={info['version']}" if info
                 else f"{'(not on LAN)':16} powered off / other network?")
        print(f"  {where}  {d.get('name', '')}  {dev_id}")


def _scene_store(args):
    from .mqtt.bridge import scenes_path
    from .scenes import SceneStore
    return SceneStore(scenes_path(args.devices))


def cmd_scenes_list(args):
    store = _scene_store(args)
    for name in store.scene_names():
        print(f"{name:32} {store.scene_payload(name) or '(saved look)'}")
    print("\nEffects: " + ", ".join(store.effect_names()))


def cmd_scenes_import(args):
    from .cloud.tuya_oem import TuyaOemApi
    from .mqtt.bridge import import_custom_scenes, scenes_path
    api = TuyaOemApi(_load_json(args.creds))
    api.login(args.email or input("Enbrighten account email: "),
              getpass.getpass("Enbrighten password: "))
    names = import_custom_scenes(api, api.devices(), scenes_path(args.devices))
    print(f"Imported {len(names)} DIY/edited scene(s): {', '.join(names) or '(none)'}")


def cmd_scenes_capture(args):
    """Name scenes as they're selected in the app, for ones not in the table."""
    from .config import devices_from_json
    from .local.device import DeviceInfo, LocalDevice
    from .local.discovery import find_device_ip
    store = _scene_store(args)
    dcfg = next(d for d in devices_from_json(args.devices)
                if not args.device or d.dev_id == args.device)
    ip, ver = find_device_ip(dcfg.dev_id)
    if not ip:
        sys.exit(f"{dcfg.dev_id} not found on the LAN")
    dev = LocalDevice(DeviceInfo(dcfg.dev_id, ip, dcfg.local_key, dcfg.version or ver))
    dev.connect()
    print("Select a scene in the app, then type a name for it here (blank to stop).")
    while name := input("name> ").strip():
        raw = str(dev.status().get("dps", {}).get("106", ""))
        if not raw or raw.startswith("ff"):
            print("  the light isn't showing a scene; skipped")
            continue
        learned = store.scene_for(raw)
        if learned != name:
            store.rename(learned, name)
        print(f"  {name} = {raw}")


def cmd_serve(args):
    from .hassio import apply_addon_options
    from .mqtt.bridge import run
    apply_addon_options()   # no-op unless running as a Home Assistant add-on
    run(args.config)


def _ver(v):
    try:
        return float(v) if v else None
    except (TypeError, ValueError):
        return None


def main(argv=None):
    p = argparse.ArgumentParser(prog="enbrighten2mqtt")
    sub = p.add_subparsers(dest="cmd", required=True)

    pe = sub.add_parser("extract-keys")
    pe.add_argument("apk")
    pe.add_argument("--out", default="secrets/oem.json")
    pe.set_defaults(func=cmd_extract_keys)

    pl = sub.add_parser("login")
    pl.add_argument("--creds", default="secrets/oem.json")
    pl.add_argument("--email")
    pl.add_argument("--out", default="devices.json")
    pl.set_defaults(func=cmd_login)

    pls = sub.add_parser("list")
    pls.add_argument("--devices", default="devices.json")
    pls.set_defaults(func=cmd_list)

    pp = sub.add_parser("probe")
    pp.add_argument("--ip", required=True)
    pp.add_argument("--device-id", required=True)
    pp.add_argument("--local-key", required=True)
    pp.add_argument("--version")
    pp.set_defaults(func=cmd_probe)

    pc = sub.add_parser("capture")
    pc.add_argument("--device")
    pc.add_argument("--ip")
    pc.add_argument("--devices", default="devices.json")
    pc.add_argument("--out", default="captures")
    pc.set_defaults(func=cmd_capture)

    pd = sub.add_parser("discover")
    pd.add_argument("--devices", default="devices.json")
    pd.set_defaults(func=cmd_discover)

    psc = sub.add_parser("scenes").add_subparsers(dest="scenes_cmd", required=True)
    for name, func in (("list", cmd_scenes_list), ("import", cmd_scenes_import),
                       ("capture", cmd_scenes_capture)):
        q = psc.add_parser(name)
        q.add_argument("--devices", default="devices.json")
        q.set_defaults(func=func)
        if name == "import":
            q.add_argument("--creds", default="secrets/oem.json")
            q.add_argument("--email")
        if name == "capture":
            q.add_argument("--device")

    ps = sub.add_parser("serve")
    ps.add_argument("--config", default="config.yaml")
    ps.set_defaults(func=cmd_serve)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args.func(args)


if __name__ == "__main__":
    main()
