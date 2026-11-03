"""
Solar plug controller
- Reads battery SOC from the LuxPower cloud (af.luxpowertek.com)
- Switches a CBI Astute smart plug (Tuya cloud) based on these rules:
    * At or after CUTOFF_TIME (13:00)      -> plug OFF
    * Battery below LOW_SOC (85%)          -> plug OFF
    * Battery at ON_SOC (100%)             -> plug ON
    * Anything in between                  -> leave plug as it is
Meant to run every ~10 minutes (GitHub Actions schedule, cron, Task Scheduler...).

Settings come from environment variables (GitHub "Secrets"):
  LUX_USER, LUX_PASS, LUX_SERIAL         LuxPower app login + inverter serial number
  TUYA_KEY, TUYA_SECRET, TUYA_REGION     Tuya developer project Access ID / Secret / data centre (eu, us, cn, in)
  TUYA_DEVICE_ID                         The plug's device ID
Optional:
  LUX_BASE_URL   default https://af.luxpowertek.com
  ON_SOC         default 100
  LOW_SOC        default 85
  CUTOFF_TIME    default 13:00
  TZ_NAME        default Africa/Johannesburg
  SWITCH_CODE    default: auto-detected (switch_1 or switch)
"""

import os
import sys
from datetime import datetime, time
from zoneinfo import ZoneInfo

import requests
import tinytuya


def env(name, default=None, required=False):
    value = os.environ.get(name, default)
    if required and not value:
        sys.exit(f"Missing setting: {name}")
    return value


LUX_BASE_URL = env("LUX_BASE_URL", "https://af.luxpowertek.com").rstrip("/")
LUX_USER = env("LUX_USER", required=True)
LUX_PASS = env("LUX_PASS", required=True)
LUX_SERIAL = env("LUX_SERIAL", required=True)

TUYA_KEY = env("TUYA_KEY", required=True)
TUYA_SECRET = env("TUYA_SECRET", required=True)
TUYA_REGION = env("TUYA_REGION", "eu")
TUYA_DEVICE_ID = env("TUYA_DEVICE_ID", required=True)

ON_SOC = int(env("ON_SOC", "100"))
LOW_SOC = int(env("LOW_SOC", "85"))
CUTOFF = time.fromisoformat(env("CUTOFF_TIME", "13:00"))
TZ = ZoneInfo(env("TZ_NAME", "Africa/Johannesburg"))
SWITCH_CODE = env("SWITCH_CODE")


# ---------- LuxPower ----------

def get_battery_soc():
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0"
    r = s.post(f"{LUX_BASE_URL}/WManage/api/login",
               data={"account": LUX_USER, "password": LUX_PASS, "language": "ENGLISH"},
               timeout=30)
    r.raise_for_status()
    r = s.post(f"{LUX_BASE_URL}/WManage/api/inverter/getInverterRuntime",
               data={"serialNum": LUX_SERIAL}, timeout=30)
    r.raise_for_status()
    try:
        data = r.json()
    except ValueError:
        sys.exit("LuxPower login failed - check LUX_USER / LUX_PASS / LUX_BASE_URL")
    if not data.get("success", True) or "soc" not in data:
        sys.exit(f"LuxPower returned no battery data: {data}")
    return int(data["soc"])


# ---------- Tuya / CBI plug ----------

cloud = tinytuya.Cloud(apiRegion=TUYA_REGION, apiKey=TUYA_KEY,
                       apiSecret=TUYA_SECRET, apiDeviceID=TUYA_DEVICE_ID)


def get_plug():
    """Returns (switch_code, is_on)."""
    status = cloud.getstatus(TUYA_DEVICE_ID)
    if not status or not status.get("success"):
        sys.exit(f"Could not read plug from Tuya: {status}")
    points = {p["code"]: p["value"] for p in status["result"]}
    code = SWITCH_CODE or next((c for c in ("switch_1", "switch") if c in points), None)
    if code is None:
        sys.exit(f"No on/off switch found on plug. Codes: {list(points)}")
    return code, bool(points[code])


def set_plug(code, on):
    result = cloud.sendcommand(TUYA_DEVICE_ID, {"commands": [{"code": code, "value": on}]})
    if not result or not result.get("success"):
        sys.exit(f"Tuya command failed: {result}")


# ---------- Rules ----------

def main():
    now = datetime.now(TZ)
    code, is_on = get_plug()

    if now.time() >= CUTOFF:
        want, reason = False, f"past cut-off {CUTOFF:%H:%M}"
    else:
        soc = get_battery_soc()
        if soc < LOW_SOC:
            want, reason = False, f"battery {soc}% < {LOW_SOC}%"
        elif soc >= ON_SOC:
            want, reason = True, f"battery {soc}% full"
        else:
            want, reason = is_on, f"battery {soc}% - no change"

    if want != is_on:
        set_plug(code, want)
        print(f"{now:%Y-%m-%d %H:%M} plug -> {'ON' if want else 'OFF'} ({reason})")
    else:
        print(f"{now:%Y-%m-%d %H:%M} plug stays {'ON' if is_on else 'OFF'} ({reason})")


if __name__ == "__main__":
    main()
