"""
Smart solar geyser controller (off-grid)
- Reads battery SOC from the LuxPower cloud
- Checks the solar forecast (Open-Meteo, free, no account)
- Switches the geyser's smart plug (Tuya / Smart Life) so the geyser is
  available as much as possible without running the battery down.

The geyser's own thermostat controls the temperature: while the plug is ON it
only draws power when the water needs heating.

Rules (battery % thresholds; between "on" and "off" the plug stays as it is):
  Any time            battery >= ALWAYS_ON (95)                 -> ON
  Day   06:00-15:00   ON at 90,  OFF below 75   (+5 if today is cloudy)
  Evening 15:00-20:00 OFF below MIN_SOC + OVERNIGHT_PCT (+5 normal / +10 cloudy tomorrow)
                      ON 10 above that
  Night 20:00-06:00   ON at 95,  OFF below 90   (protects the battery overnight)

Required settings (GitHub secrets):
  LUX_USER, LUX_PASS, LUX_SERIAL, TUYA_KEY, TUYA_SECRET, TUYA_REGION, TUYA_DEVICE_ID
Optional:
  MIN_SOC        lowest battery % you accept by sunrise          (default 40)
  OVERNIGHT_PCT  battery % the house uses from 20:00 to 06:00    (default 30)
  LAT, LON       location for the weather forecast               (default Roodepoort)
  LUX_BASE_URL   LuxPower server (auto-detected if not set)
  SWITCH_CODE    plug switch code (auto-detected)
"""

import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
import tinytuya


def env(name, default=None, required=False):
    value = os.environ.get(name) or default
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
SWITCH_CODE = env("SWITCH_CODE")

MIN_SOC = int(env("MIN_SOC", "40"))
OVERNIGHT_PCT = int(env("OVERNIGHT_PCT", "30"))
ALWAYS_ON = 95
LAT = float(env("LAT", "-26.16"))
LON = float(env("LON", "27.87"))
TZ = ZoneInfo("Africa/Johannesburg")

# Daily solar radiation (MJ/m2) - Gauteng clear summer day is ~28-30
SUNNY_MJ = 22
CLOUDY_MJ = 14


# ---------- LuxPower ----------

LUX_SERVERS = [LUX_BASE_URL] + [u for u in (
    "https://af.luxpowertek.com",
    "https://eu.luxpowertek.com",
    "https://sea.luxpowertek.com",
    "https://us.luxpowertek.com",
    "https://na.luxpowertek.com",
    "https://server.luxpowertek.com",
) if u != LUX_BASE_URL]

LUX_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
}


def lux_login():
    problems = []
    for base in LUX_SERVERS:
        s = requests.Session()
        s.headers.update(LUX_HEADERS)
        try:
            r = s.post(f"{base}/WManage/api/login",
                       data={"account": LUX_USER, "password": LUX_PASS, "language": "ENGLISH"},
                       timeout=30)
            data = r.json()
        except Exception as e:
            problems.append(f"{base}: {type(e).__name__}")
            continue
        if data.get("success"):
            return s, base
        problems.append(f"{base}: {data.get('msg') or data.get('message') or data}")
    sys.exit("LuxPower login failed on every server - check LUX_USER / LUX_PASS.\n  "
             + "\n  ".join(problems))


def get_battery_soc():
    s, base = lux_login()
    r = s.post(f"{base}/WManage/api/inverter/getInverterRuntime",
               data={"serialNum": LUX_SERIAL}, timeout=30)
    try:
        data = r.json()
    except ValueError:
        sys.exit(f"LuxPower gave no data (HTTP {r.status_code}) - check LUX_SERIAL")
    if not data.get("success", True) or "soc" not in data:
        sys.exit(f"LuxPower returned no battery data - check LUX_SERIAL. Reply: {data}")
    return int(data["soc"])


# ---------- Weather (Open-Meteo) ----------

def get_solar_outlook():
    """Returns {'today': 'sunny'|'normal'|'cloudy', 'tomorrow': ...}.
    If the forecast can't be fetched, assume cloudy (the safe choice off-grid)."""
    try:
        r = requests.get("https://api.open-meteo.com/v1/forecast", params={
            "latitude": LAT, "longitude": LON,
            "daily": "shortwave_radiation_sum",
            "timezone": "Africa/Johannesburg", "forecast_days": 2,
        }, timeout=20)
        mj = r.json()["daily"]["shortwave_radiation_sum"]

        def label(v):
            return "sunny" if v >= SUNNY_MJ else "cloudy" if v < CLOUDY_MJ else "normal"
        return {"today": label(mj[0]), "tomorrow": label(mj[1]),
                "mj": f"{mj[0]:.0f}/{mj[1]:.0f} MJ"}
    except Exception as e:
        print(f"Weather forecast unavailable ({type(e).__name__}) - assuming cloudy")
        return {"today": "cloudy", "tomorrow": "cloudy", "mj": "n/a"}


# ---------- Tuya / plug ----------

cloud = tinytuya.Cloud(apiRegion=TUYA_REGION, apiKey=TUYA_KEY,
                       apiSecret=TUYA_SECRET, apiDeviceID=TUYA_DEVICE_ID)


def get_plug():
    """Returns (switch_code, is_on, watts or None)."""
    status = cloud.getstatus(TUYA_DEVICE_ID)
    if not status or not status.get("success"):
        sys.exit(f"Could not read plug from Tuya: {status}")
    points = {p["code"]: p["value"] for p in status["result"]}
    code = SWITCH_CODE or next((c for c in ("switch_1", "switch") if c in points), None)
    if code is None:
        sys.exit(f"No on/off switch found on plug. Codes: {list(points)}")
    watts = points.get("cur_power")
    if watts is not None:
        watts = watts / 10  # Tuya reports tenths of a watt
    return code, bool(points[code]), watts


def set_plug(code, on):
    result = cloud.sendcommand(TUYA_DEVICE_ID, {"commands": [{"code": code, "value": on}]})
    if not result or not result.get("success"):
        sys.exit(f"Tuya command failed: {result}")


# ---------- Rules ----------

def thresholds(hour, outlook):
    """Returns (period, on_at, off_below)."""
    if 6 <= hour < 15:
        extra = 5 if outlook["today"] == "cloudy" else 0
        return f"day ({outlook['today']} today)", 90 + extra, 75 + extra
    if 15 <= hour < 20:
        extra = {"sunny": 0, "normal": 5, "cloudy": 10}[outlook["tomorrow"]]
        off = MIN_SOC + OVERNIGHT_PCT + extra
        return f"evening ({outlook['tomorrow']} tomorrow)", min(off + 10, ALWAYS_ON), off
    return "night", ALWAYS_ON, 90


def main():
    now = datetime.now(TZ)
    code, is_on, watts = get_plug()
    soc = get_battery_soc()
    outlook = get_solar_outlook()
    period, on_at, off_below = thresholds(now.hour, outlook)

    if soc >= ALWAYS_ON or soc >= on_at:
        want = True
    elif soc < off_below:
        want = False
    else:
        want = is_on

    draw = f", geyser drawing {watts:.0f} W" if (watts is not None and is_on) else ""
    info = (f"battery {soc}%, {period}: on at {on_at}%, off below {off_below}%"
            f" [solar {outlook['mj']}]{draw}")

    if want != is_on:
        set_plug(code, want)
        print(f"{now:%Y-%m-%d %H:%M} plug -> {'ON' if want else 'OFF'} ({info})")
    else:
        print(f"{now:%Y-%m-%d %H:%M} plug stays {'ON' if is_on else 'OFF'} ({info})")


if __name__ == "__main__":
    main()
