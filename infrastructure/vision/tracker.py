import math
from datetime import datetime

import cv2

from domain.interfaces import ITracker


class Track:
    def __init__(self, track_id: int, bbox, hist, now: datetime):
        self.id = track_id
        self.bbox = bbox
        self.hist = hist

        self.first_seen = now
        self.last_seen = now

        self.hits = 1
        self.misses = 0
        self.det_idx = None

        self.identity_type = None
        self.employee_id = None
        self.unknown_id = None
        self.confidence = None

        self.last_recognition_time = None
        self.unknown_created = False

    @property
    def key(self):
        return f"T{self.id:06d}"

    def age_seconds(self, now: datetime) -> float:
        return (now - self.first_seen).total_seconds()

    def center(self):
        x, y, w, h = self.bbox
        return x + w / 2, y + h / 2

    def is_stable(self, now: datetime, min_age_sec: float = 1.0, min_hits: int = 2) -> bool:
        return self.hits >= min_hits and self.age_seconds(now) >= min_age_sec

    @staticmethod
    def _iou(box1, box2):
        x1, y1, w1, h1 = box1
        x2, y2, w2, h2 = box2

        xa1, ya1, xa2, ya2 = x1, y1, x1 + w1, y1 + h1
        xb1, yb1, xb2, yb2 = x2, y2, x2 + w2, y2 + h2

        ix1 = max(xa1, xb1)
        iy1 = max(ya1, yb1)
        ix2 = min(xa2, xb2)
        iy2 = min(ya2, yb2)

        if ix2 <= ix1 or iy2 <= iy1:
            return 0.0

        inter = (ix2 - ix1) * (iy2 - iy1)
        area1 = w1 * h1
        area2 = w2 * h2
        union = area1 + area2 - inter

        if union <= 0:
            return 0.0

        return inter / union

    def match_score(self, detection, frame_diag: float) -> float:
        iou = self._iou(self.bbox, detection.bbox)

        corr = 0.0
        try:
            if self.hist is not None and detection.hist is not None:
                corr = cv2.compareHist(self.hist, detection.hist, cv2.HISTCMP_CORREL)
        except Exception:
            corr = 0.0

        cx2 = detection.bbox[0] + detection.bbox[2] / 2
        cy2 = detection.bbox[1] + detection.bbox[3] / 2
        cx1, cy1 = self.center()

        dist = math.hypot(cx1 - cx2, cy1 - cy2)
        norm_dist = dist / max(1.0, frame_diag)

        if iou < 0.05 and corr < 0.45 and norm_dist > 0.25:
            return -1.0

        score = (iou * 0.65) + (max(0.0, corr) * 0.35) - (norm_dist * 0.2)
        return score

    def update(self, detection, now: datetime):
        self.bbox = detection.bbox
        self.hist = detection.hist
        self.last_seen = now
        self.misses = 0
        self.hits += 1

    def need_recognition(self, now: datetime, reid_interval_sec: int) -> bool:
        if self.identity_type is None:
            return True

        if self.last_recognition_time is None:
            return True

        if (now - self.last_recognition_time).total_seconds() >= reid_interval_sec:
            return True

        return False


class SimpleTracker(ITracker):
    def __init__(self, max_misses: int = 12):
        self._tracks = []
        self.next_id = 1
        self.max_misses = max_misses

    @property
    def tracks(self):
        return self._tracks

    def has_active(self) -> bool:
        return len(self._tracks) > 0

    def update(self, detections, now: datetime, frame_shape=(480, 640)):
        frame_h, frame_w = frame_shape
        frame_diag = math.hypot(frame_h, frame_w)

        for track in self._tracks:
            track.det_idx = None

        pairs = []

        for track in self._tracks:
            for det_idx, det in enumerate(detections):
                score = track.match_score(det, frame_diag)
                if score > 0.25:
                    pairs.append((score, track.id, det_idx))

        pairs.sort(key=lambda item: item[0], reverse=True)

        track_by_id = {track.id: track for track in self._tracks}

        used_tracks = set()
        used_dets = set()

        for score, track_id, det_idx in pairs:
            if track_id in used_tracks or det_idx in used_dets:
                continue

            track = track_by_id.get(track_id)
            if not track:
                continue

            det = detections[det_idx]
            track.update(det, now)
            track.det_idx = det_idx

            used_tracks.add(track_id)
            used_dets.add(det_idx)

        for det_idx, det in enumerate(detections):
            if det_idx in used_dets:
                continue

            new_track = Track(self.next_id, det.bbox, det.hist, now)
            new_track.det_idx = det_idx
            self._tracks.append(new_track)

            used_tracks.add(new_track.id)
            used_dets.add(det_idx)

            self.next_id += 1

        for track in self._tracks:
            if track.id not in used_tracks:
                track.misses += 1

        self._tracks = [
            track for track in self._tracks
            if track.misses <= self.max_misses
        ]

        return self._tracks