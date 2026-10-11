# Smart parking, enforcement, and citizen reporting

Local demo of the blueprint: seeded Karaikal and Puducherry zones, occupancy status, a citizen map, a police console, grace-period violations, complaint merge, and a CCTV worker.

## Start

```bash
./scripts/start.sh
```

That command starts PostGIS, loads a clean seed, and serves:

- API at http://127.0.0.1:8000
- Citizen map at http://127.0.0.1:5173/
- Police console at http://127.0.0.1:5173/police

Run it again to reseed. Both launches return the same zone codes.

```bash
./scripts/stop.sh
```

## Camera worker

```bash
./scripts/start-camera.sh
PYTHONPATH=. .venv/bin/python -m worker.main
```

`start-camera.sh` publishes `media/clips/cam1.mp4` with MediaMTX and `ffmpeg -re -stream_loop`. The worker samples that RTSP path with YOLO26n, ByteTrack (`persist=True`), and a bottom-centre polygon zone, then posts occupancy to `/ingest/occupancy`.

Install the vision packages once, into the project virtualenv:

```bash
.venv/bin/pip install -r requirements-cv.txt
```

## Demo controls

`POST /demo/simulate-vehicle` with `{"zone_id":"KKL-MARKET","action":"park"}` runs the dwell rules (grace from `DEMO_GRACE_SECONDS`, default 1) and opens one possible-violation incident. `action` `leave` is passing traffic. `POST /demo/reset` reloads the seed. `POST /demo/step/1` through `/6` jumps the script.

Officer `POST /incidents/{id}/verify` and `/dismiss` record an audit row. Nothing in this build submits a challan.
