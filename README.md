# Solar plug controller

Turns your CBI smart plug **on** when the LuxPower battery hits **100%**, and **off** when it drops below **85%** or at **13:00**, whichever comes first. Runs free on GitHub every 10 minutes between 06:00 and 13:00. No PC needs to stay on.

## 1. Link the CBI plug to a Tuya developer account (~15 min, once)

1. Go to **iot.tuya.com** and sign up (free).
2. **Cloud → Development → Create Cloud Project**
   - Industry: Smart Home · Development method: Smart Home
   - Data centre: **Central Europe** (this is what most SA accounts use)
3. Open the project → **Overview** tab. Copy the **Access ID** and **Access Secret**.
4. **Devices → Link App Account → Add App Account**. A QR code appears.
5. In the **CBI Home** app: *Me* tab → scan icon (top right) → scan the QR code → confirm.
6. Your plug now shows in the Devices list. Copy its **Device ID**.
7. **Service API** tab: make sure *IoT Core* and *Authorization Token Management* are subscribed (free trial; renew it every 6 months from the same page).

## 2. Get your LuxPower details

- The username and password you use in the LuxPower app.
- Your inverter **serial number** (10 digits, shown in the app under the device).

## 3. Put it on GitHub (~10 min, once)

1. Sign up at **github.com** (free).
2. Create a new **private** repository called `solar-plug`.
3. Upload these files, keeping the folder structure: `luxplug.py` and `.github/workflows/solar-plug.yml`.
4. **Settings → Secrets and variables → Actions → New repository secret.** Add each one:

| Secret          | Value                                     |
|-----------------|-------------------------------------------|
| LUX_USER        | LuxPower app username                     |
| LUX_PASS        | LuxPower app password                     |
| LUX_SERIAL      | Inverter serial number                    |
| TUYA_KEY        | Tuya Access ID                            |
| TUYA_SECRET     | Tuya Access Secret                        |
| TUYA_REGION     | `eu` (for Central Europe data centre)     |
| TUYA_DEVICE_ID  | The plug's Device ID                      |

## 4. Test it

**Actions** tab → *Solar plug* → **Run workflow**. Open the run to see a line like:

```
2026-10-31 09:20 plug stays OFF (battery 92% - no change)
```

A red run means a setting is wrong; the error message says which one.

## Notes

- **Changing the rules:** add secrets named `ON_SOC`, `LOW_SOC` or `CUTOFF_TIME` (e.g. `14:00`). If you change the cut-off, also change the hours in the workflow file.
- **Timing:** GitHub's scheduler can run a few minutes late at busy times. Fine for this, but not to-the-minute.
- **Keep it alive:** GitHub pauses schedules on repos with no activity for 60 days. It emails you first; one click re-enables it.
- **Manual control still works.** If you switch the plug on by hand mid-morning, the script leaves it on unless the battery drops below 85% or it's 13:00.
- **Battery between 85% and 99%:** the plug stays as it is. That stops it flicking on and off.
- LuxPower has no official API. This uses the same address the LuxPower website uses, so a LuxPower update could break it.
