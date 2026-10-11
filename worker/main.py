"""Read one RTSP camera, run YOLO26n + ByteTrack + polygon zones, post facts.

The worker does not decide status or create priority. It posts counts and dwell
alerts into the same API the demo controls use.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import httpx
import numpy as np

from rules.dwell import DwellTracker, TrackObs


def _parse() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smart parking CV worker")
    parser.add_argument("--rtsp", default=os.environ.get("RTSP_URL", "rtsp://127.0.0.1:8554/cam1"))
    parser.add_argument("--api", default=os.environ.get("API_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--zone", default=os.environ.get("ZONE_CODE", "KKL-BEACH-N"))
    parser.add_argument("--nopark-zone", default=os.environ.get("NOPARK_ZONE", "KKL-MARKET"))
    parser.add_argument("--model", default=os.environ.get("YOLO_MODEL", "yolo26n.pt"))
    parser.add_argument("--frames", type=int, default=int(os.environ.get("WORKER_FRAMES", "1")))
    parser.add_argument("--key", default=os.environ.get("WORKER_KEY", "dev-worker-key"))
    parser.add_argument("--grace", type=float, default=float(os.environ.get("DEMO_GRACE_SECONDS", "120")))
    return parser.parse_args()


def _read_frame(cap, attempts: int = 40):
    for _ in range(attempts):
        ok, frame = cap.read()
        if ok and frame is not None and getattr(frame, "size", 0) > 0:
            return frame
        time.sleep(0.1)
    return None


def _polygon(width: int, height: int) -> np.ndarray:
    x0, y0 = int(width * 0.06), int(height * 0.08)
    x1, y1 = int(width * 0.94), int(height * 0.92)
    return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=np.int32)


def step_dwell(tracker, tracker_ids, anchors, class_ids, inside, t: float) -> list[dict]:
    """Advance dwell for one processed frame.

    An empty frame, a frame with no track ids, and a frame whose anchors are
    all outside the zone still call ``DwellTracker.update`` with an empty
    observation list. Skipping that call would freeze ``last_seen`` and let a
    track that was gone longer than the lost-track tolerance keep its timer.
    """
    observations: list[TrackObs] = []
    if tracker_ids is not None and anchors is not None and inside is not None:
        classes = class_ids if class_ids is not None else [2] * len(tracker_ids)
        for track_id, anchor, class_id, is_inside in zip(tracker_ids, anchors, classes, inside):
            if track_id is None or not is_inside:
                continue
            observations.append(TrackObs(int(track_id), (float(anchor[0]), float(anchor[1])), int(class_id)))
    return tracker.update(observations, float(t))


def main() -> int:
    args = _parse()
    os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
    import cv2
    import supervision as sv
    from ultralytics import YOLO

    print(f"opening rtsp {args.rtsp}", flush=True)
    capture = cv2.VideoCapture(args.rtsp, cv2.CAP_FFMPEG)
    frame = _read_frame(capture)
    if frame is None:
        print("no frame from rtsp", flush=True)
        return 1

    model = YOLO(args.model)
    print(f"model loaded {args.model}", flush=True)
    polygon = _polygon(frame.shape[1], frame.shape[0])
    zone = sv.PolygonZone(polygon=polygon, triggering_anchors=(sv.Position.BOTTOM_CENTER,))
    dwell = DwellTracker(grace_s=args.grace, lost_tolerance_s=15, move_tol_px=25, zone=args.nopark_zone)
    headers = {"X-Worker-Key": args.key}
    occupied = 0
    processed = 0
    target = max(1, args.frames)

    while processed < target:
        if processed > 0:
            nxt = _read_frame(capture, attempts=15)
            if nxt is None:
                break
            frame = nxt
        results = model.track(
            frame,
            persist=True,
            classes=[1, 2, 3, 5, 7],
            conf=0.25,
            imgsz=640,
            tracker="bytetrack.yaml",
            verbose=False,
        )
        processed += 1
        print(f"frame processed {processed}", flush=True)
        detections = sv.Detections.from_ultralytics(results[0])
        if len(detections) == 0 or detections.tracker_id is None:
            occupied = 0 if len(detections) == 0 else int(zone.trigger(detections).sum())
            alerts = step_dwell(dwell, None, None, None, None, float(processed))
        else:
            inside = zone.trigger(detections)
            occupied = int(inside.sum())
            alerts = step_dwell(
                dwell,
                detections.tracker_id,
                detections.get_anchors_coordinates(sv.Position.BOTTOM_CENTER),
                detections.class_id,
                inside,
                float(processed),
            )
        # Sampled CCTV is not wall-clock; advance by one second per frame so a
        # short demo grace can still be reached across frames.
        for alert in alerts:
            print(f"dwell post attempted track={alert['track_id']} dwell_s={alert['dwell_s']}", flush=True)
            response = httpx.post(
                f"{args.api}/ingest/violation",
                json={
                    "zone_code": args.nopark_zone,
                    "track_id": alert["track_id"],
                    "dwell_s": alert["dwell_s"],
                    "class_id": alert["class_id"],
                },
                headers=headers,
                timeout=30,
            )
            print(f"dwell post status {response.status_code}", flush=True)

    capture.release()
    print(f"occupancy post attempted zone={args.zone} occupied={occupied}", flush=True)
    response = httpx.post(
        f"{args.api}/ingest/occupancy",
        json=[
            {
                "zone_code": args.zone,
                "occupied": occupied,
                "occupied_2w": 0,
                "confidence": 0.5,
                "source": "cctv",
            }
        ],
        headers=headers,
        timeout=30,
    )
    print(f"occupancy post status {response.status_code} body={response.text[:300]}", flush=True)
    return 0 if response.status_code == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
