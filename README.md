![zepp-health-ha icon](custom_components/zepp_health/brand/icon.png)

# Zepp Health Import for Home Assistant

![GitHub branch check runs](https://img.shields.io/github/check-runs/ndesgranges/zepp-health-ha/master?style=for-the-badge)

[![GitHub Release](https://img.shields.io/github/v/release/ndesgranges/zepp-health-ha?display_name=tag&style=for-the-badge
)](https://github.com/ndesgranges/zepp-health-ha/releases)


[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)

Import **full historical health data** from Zepp (Amazfit/Huami) watches into Home Assistant's long-term statistics. All data appears in the built-in history graphs, statistics panels, and is usable in automations.

## Metrics

| Category | Sensors |
|----------|---------|
| **Training Load** | ATL (Acute/Fatigue), CTL (Chronic/Fitness), TSB (Form/Freshness), Daily Score |
| **BioCharge** | Score, Wake BioCharge, Max/Min, Energy Consumed |
| **Readiness** | Overall Score, Sleep/HRV/RHR/Recovery/Regularity components |
| **Sleep** | Total, Deep, Light, REM, Awake minutes, Score, Sleep HR, Latency, Regularity |
| **Heart** | HRV (RMSSD), Resting HR (from sleep) |
| **Activity** | Daily Steps, Distance, Calories, PAI (daily + weekly) |
| **SpO2** | Blood Oxygen Score |

## Prerequisites

You need to extract the data from your watch databases first:

1. **Root & ADB access** to pull databases from the Zepp app:
   ```bash
   adb pull /data/data/com.huami.watch.hmwatchmanager/databases ./zepp-databases
   ```

2. **Run the extraction script** (included in this repo under `scripts/`):
   ```bash
   python3 scripts/extract_zepp_health.py --db-dir ./zepp-databases
   ```
   This produces a `zepp_health_export.json` file.

## Installation

### HACS (Recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ndesgranges&repository=zepp-health-ha&category=integration)

OR

1. Install HACS if you don't have it already
2. Open HACS in Home Assistant
3. On the top right side, click the three dot and click `Custom repositories`
4. Where asked for a URL, paste the link of this repository:
https://github.com/ndesgranges/zepp-health-ha
5. Where asked for a type, select `integration`
6. Click the download button. ⬇️

### Manual

Copy the `custom_components/zepp_health/` folder into your HA `config/custom_components/` directory.

## Setup

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **Zepp Health Import**
3. Choose **Upload JSON file** and select your `zepp_health_export.json`
4. All historical data will be imported automatically

## Updating Data

After pulling fresh databases and re-running the extraction script:

1. Go to **Developer Tools → Services**
2. Call `zepp_health.import_file`
3. Upload the new `zepp_health_export.json`
4. All new data points are imported and sensors update

Alternatively, call `zepp_health.import_history` to re-import from the previously uploaded file.

## How It Works

- On setup (or `import_file` call), the integration reads the full JSON export
- Each metric's time series is imported as **long-term statistics** via `async_import_statistics`
- These appear in HA's built-in history panel, energy dashboard, and statistics graphs
- Sensor entities show the latest values and are available for automations/dashboards

## Database Mapping

The extraction script reads from these Zepp databases:

| Database | Data |
|----------|------|
| `phndata-*.db` | ATL, CTL, TSB, recovery factor |
| `companion-aa.db` | Sleep stages, steps, daily sport load |
| `HealthMatrix_*.db` | BioCharge, readiness, HRV, PAI, SpO2 |
| `RestHeartDb` | Resting heart rate |

## License

MIT
