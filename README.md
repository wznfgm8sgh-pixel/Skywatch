# Skywatch

A self-hosted ADS-B aircraft tracking portal with a dark "ATC ops room" look —
live map with range rings and buff flight-progress strips.

Data comes from a Raspberry Pi running dump1090/readsb + fr24feed. The Pi is
never modified; Skywatch reads from it over the local network.

---

## Quick start on TrueNAS (raw-fetch method)

1. **Apps → Discover → three-dot menu → Install via YAML**
2. Paste the contents of [`truenas-app.yaml`](truenas-app.yaml) (Method A block).
3. Edit the `environment` section with your Pi's IP, receiver position, and
   site name.
4. Click **Save**. Wait for the container to reach **Running**.
5. Open `http://<truenas-ip>:8088/` — the footer's "Feed check" line shows
   which data source was locked onto.

**To pick up code changes:** edit on GitHub → **Restart** the TrueNAS app.
The container re-downloads `bridge.py` and `skywatch.html` on each start.

### If the portal shows DEMO instead of LIVE

The bridge couldn't reach an `aircraft.json` on the Pi. Try in order:

1. Confirm `ADSB_HOST` is correct and the Pi is reachable from the NAS.
2. Check whether `http://<pi-ip>:8080/data/aircraft.json` is accessible from
   your browser — that's the first HTTP source the bridge tries.
3. If you rely on fr24feed's embedded decoder, enable BaseStation output:
   add `bs="yes"` to `/etc/fr24feed.ini` on the Pi and restart fr24feed.
   The bridge will then fall back to the TCP 30003 stream.

---

## Alternative: pre-built GHCR image

GitHub Actions (`.github/workflows/build.yml`) builds the image and pushes it
to GHCR on every push to `main`. Once the first build has run, go to the
package settings on GitHub and set it **Public**, then switch to Method B in
`truenas-app.yaml`.

---

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `ADSB_HOST` | `127.0.0.1` | IP of the decoder host (Pi) |
| `ADSB_SBS_PORT` | `30003` | BaseStation TCP port |
| `PORT` | `8088` | Port this bridge serves on |
| `RECEIVER_LAT` | — | Receiver latitude (decimal degrees) |
| `RECEIVER_LON` | — | Receiver longitude (decimal degrees) |
| `RECEIVER_NAME` | — | Short location label shown on the page |
| `SITE_NAME` | — | Page heading |
| `SITE_TAGLINE` | — | Sub-heading |

---

## Files

| File | Description |
|---|---|
| `bridge.py` | Python stdlib-only HTTP server; serves the page and `/data/aircraft.json` |
| `skywatch.html` | Single-file front-end (Leaflet map, flight strips, demo mode) |
| `Dockerfile` | Builds a self-contained image for the GHCR route |
| `truenas-app.yaml` | Docker Compose for TrueNAS (both deployment methods) |
