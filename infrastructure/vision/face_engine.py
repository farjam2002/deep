import os
import threading
from datetime import datetime

import cv2
import numpy as np

from domain.interfaces import IFaceEngine
from domain.types import Detection


class FaceEngineManager(IFaceEngine):
    def __init__(self, cfg: dict):
        self.cfg = cfg

        self.face_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )

        self.lbph_available = hasattr(cv2, "face") and hasattr(cv2.face, "LBPHFaceRecognizer_create")

        self.ort_session = None
        self.onnx_available = False

        self.active_backend = "none"

        self.model = None

        self.embeddings = None
        self.embedding_labels = []

        self.label_map = {}

        self.dirty = True
        self.last_train = None

        self.train_lock = threading.RLock()
        self.detect_lock = threading.Lock()
        self.predict_lock = threading.Lock()

        self._init_onnx()
        self._choose_backend()

    def _init_onnx(self):
        proc = self.cfg.get("processing", {})
        model_path = proc.get("onnx_model_path", "")

        try:
            import onnxruntime as ort

            if model_path and os.path.exists(model_path):
                self.ort_session = ort.InferenceSession(
                    model_path,
                    providers=["CPUExecutionProvider"]
                )
                self.onnx_available = True
        except Exception:
            self.onnx_available = False

    def _choose_backend(self):
        proc = self.cfg.get("processing", {})
        backend = proc.get("recognition_backend", "auto")

        if backend == "onnx" and self.onnx_available:
            self.active_backend = "onnx"
        elif backend == "lbph" and self.lbph_available:
            self.active_backend = "lbph"
        elif backend == "auto":
            if self.onnx_available:
                self.active_backend = "onnx"
            elif self.lbph_available:
                self.active_backend = "lbph"
            else:
                self.active_backend = "none"
        else:
            self.active_backend = "none"

    def mark_dirty(self):
        self.dirty = True

    def should_train(self) -> bool:
        if self.active_backend == "none":
            return False

        if not self.dirty:
            return False

        if self.last_train is None:
            return True

        interval = self.cfg.get("processing", {}).get("train_interval_sec", 10)
        elapsed = (datetime.utcnow() - self.last_train).total_seconds()
        return elapsed >= interval

    def _preprocess_lbph_face(self, face_gray):
        if face_gray is None:
            return None

        if len(face_gray.shape) == 3:
            face_gray = cv2.cvtColor(face_gray, cv2.COLOR_BGR2GRAY)

        face_gray = cv2.resize(face_gray, (128, 128))
        face_gray = cv2.equalizeHist(face_gray)
        return face_gray

    def _largest_face_gray(self, gray_img):
        if gray_img is None:
            return None

        if len(gray_img.shape) == 3:
            gray_img = cv2.cvtColor(gray_img, cv2.COLOR_BGR2GRAY)

        try:
            faces = self.face_cascade.detectMultiScale(
                gray_img,
                scaleFactor=1.1,
                minNeighbors=4,
                minSize=(30, 30)
            )
        except Exception:
            faces = []

        if len(faces) == 0:
            return gray_img

        faces = sorted(faces, key=lambda f: f[2] * f[3], reverse=True)
        x, y, w, h = faces[0]
        return gray_img[y:y + h, x:x + w]

    def _largest_face_color(self, color_img):
        if color_img is None:
            return None

        if len(color_img.shape) == 2:
            color_img = cv2.cvtColor(color_img, cv2.COLOR_GRAY2BGR)

        gray = cv2.cvtColor(color_img, cv2.COLOR_BGR2GRAY)

        try:
            faces = self.face_cascade.detectMultiScale(
                gray,
                scaleFactor=1.1,
                minNeighbors=4,
                minSize=(30, 30)
            )
        except Exception:
            faces = []

        if len(faces) == 0:
            return color_img

        faces = sorted(faces, key=lambda f: f[2] * f[3], reverse=True)
        x, y, w, h = faces[0]
        return color_img[y:y + h, x:x + w]

    def train(self):
        with self.train_lock:
            if self.active_backend == "onnx":
                self._train_onnx_locked()
            elif self.active_backend == "lbph":
                self._train_lbph_locked()
            else:
                self.model = None
                self.embeddings = None
                self.embedding_labels = []
                self.label_map = {}

            self.dirty = False
            self.last_train = datetime.utcnow()

    def _train_lbph_locked(self):
        if not self.lbph_available:
            self.model = None
            self.label_map = {}
            return

        from domain.models import Employee, UnknownPerson

        samples = []
        labels = []
        label_map = {}

        employees = Employee.query.filter_by(active=True).all()

        for emp in employees:
            label = int(emp.id)
            label_map[label] = ("employee", emp.id)

            for face in emp.faces:
                img = cv2.imread(face.image_path, cv2.IMREAD_GRAYSCALE)
                if img is None:
                    continue

                crop = self._largest_face_gray(img)
                if crop is None:
                    continue

                processed = self._preprocess_lbph_face(crop)
                if processed is None:
                    continue

                samples.append(processed)
                labels.append(label)

        unknowns = UnknownPerson.query.filter(
            UnknownPerson.active == True,  # noqa: E712
            UnknownPerson.assigned_employee_id.is_(None),
            UnknownPerson.merged_into_id.is_(None)
        ).all()

        for unk in unknowns:
            label = 1_000_000 + int(unk.id)
            label_map[label] = ("unknown", unk.id)

            if not unk.sample_image_path:
                continue

            img = cv2.imread(unk.sample_image_path, cv2.IMREAD_GRAYSCALE)
            if img is None:
                continue

            crop = self._largest_face_gray(img)
            if crop is None:
                continue

            processed = self._preprocess_lbph_face(crop)
            if processed is None:
                continue

            samples.append(processed)
            labels.append(label)

        if samples:
            try:
                self.model = cv2.face.LBPHFaceRecognizer_create()
                self.model.train(samples, np.array(labels, dtype=np.int32))
                self.label_map = label_map
            except Exception:
                self.model = None
                self.label_map = {}
        else:
            self.model = None
            self.label_map = {}

    def _embedding(self, face_bgr):
        if self.ort_session is None:
            return None

        if face_bgr is None or face_bgr.size == 0:
            return None

        proc = self.cfg.get("processing", {})
        mean = float(proc.get("onnx_input_mean", 127.5))
        std = float(proc.get("onnx_input_std", 128.0))

        try:
            img = cv2.resize(face_bgr, (112, 112))
            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32)
            img = (img - mean) / std
            img = img.transpose(2, 0, 1)
            blob = np.expand_dims(img, axis=0)

            input_name = self.ort_session.get_inputs()[0].name
            outputs = self.ort_session.run(None, {input_name: blob})

            emb = np.array(outputs[0]).flatten().astype(np.float32)

            norm = np.linalg.norm(emb)
            if norm > 0:
                emb = emb / norm

            return emb

        except Exception:
            return None

    def _embedding_from_file(self, path: str):
        img = cv2.imread(path, cv2.IMREAD_COLOR)
        if img is None:
            return None

        crop = self._largest_face_color(img)
        if crop is None:
            return None

        return self._embedding(crop)

    def _train_onnx_locked(self):
        from domain.models import Employee, UnknownPerson

        embeddings = []
        labels = []
        label_map = {}

        employees = Employee.query.filter_by(active=True).all()

        for emp in employees:
            label = int(emp.id)
            label_map[label] = ("employee", emp.id)

            for face in emp.faces:
                emb = self._embedding_from_file(face.image_path)
                if emb is None:
                    continue

                embeddings.append(emb)
                labels.append(label)

        unknowns = UnknownPerson.query.filter(
            UnknownPerson.active == True,  # noqa: E712
            UnknownPerson.assigned_employee_id.is_(None),
            UnknownPerson.merged_into_id.is_(None)
        ).all()

        for unk in unknowns:
            label = 1_000_000 + int(unk.id)
            label_map[label] = ("unknown", unk.id)

            if not unk.sample_image_path:
                continue

            emb = self._embedding_from_file(unk.sample_image_path)
            if emb is None:
                continue

            embeddings.append(emb)
            labels.append(label)

        if embeddings:
            self.embeddings = np.vstack(embeddings).astype(np.float32)
            self.embedding_labels = labels
            self.label_map = label_map
        else:
            self.embeddings = None
            self.embedding_labels = []
            self.label_map = {}

    def _quality_score(self, face_gray, w, h) -> int:
        try:
            if face_gray is None or w <= 0 or h <= 0:
                return 0

            proc = self.cfg.get("processing", {})
            min_face_size = int(proc.get("face_min_size", 60))
            blur_threshold = float(proc.get("blur_threshold", 80))

            resized = cv2.resize(face_gray, (64, 64))
            blur = cv2.Laplacian(resized, cv2.CV_64F).var()

            size_score = min(100.0, ((w * h) / max(1, min_face_size * min_face_size)) * 50.0)
            blur_score = min(100.0, (blur / max(1.0, blur_threshold)) * 50.0)

            brightness = float(face_gray.mean())
            brightness_score = max(0.0, 100.0 - abs(128.0 - brightness))

            score = (0.4 * blur_score) + (0.4 * size_score) + (0.2 * brightness_score)
            return int(score)

        except Exception:
            return 0

    def _appearance_hist(self, face_bgr):
        try:
            if face_bgr is None:
                return None

            face_bgr = cv2.resize(face_bgr, (64, 64))
            hsv = cv2.cvtColor(face_bgr, cv2.COLOR_BGR2HSV)

            hist = cv2.calcHist([hsv], [0, 1], None, [8, 8], [0, 180, 0, 256])
            cv2.normalize(hist, hist, 0, 1, cv2.NORM_MINMAX)

            return hist

        except Exception:
            return None

    def detect_faces(self, bgr_frame, gray_frame, mask=None):
        detections = []

        if bgr_frame is None or gray_frame is None:
            return detections

        proc = self.cfg.get("processing", {})
        min_face_size = int(proc.get("face_min_size", 60))
        min_quality = int(proc.get("min_quality", 25))

        try:
            with self.detect_lock:
                faces = self.face_cascade.detectMultiScale(
                    gray_frame,
                    scaleFactor=1.1,
                    minNeighbors=4,
                    minSize=(min_face_size, min_face_size)
                )
        except Exception:
            faces = []

        for (x, y, w, h) in faces:
            try:
                cx = int(x + w / 2)
                cy = int(y + h / 2)

                if mask is not None:
                    if 0 <= cy < mask.shape[0] and 0 <= cx < mask.shape[1]:
                        if mask[cy, cx] == 0:
                            continue
                    else:
                        continue

                face_gray = gray_frame[y:y + h, x:x + w]
                face_bgr = bgr_frame[y:y + h, x:x + w]

                if face_gray is None or face_bgr is None:
                    continue

                quality = self._quality_score(face_gray, w, h)
                if quality < min_quality:
                    continue

                hist = self._appearance_hist(face_bgr)

                detections.append(
                    Detection(
                        bbox=(int(x), int(y), int(w), int(h)),
                        face_gray=face_gray,
                        face_bgr=face_bgr,
                        hist=hist,
                        quality=quality
                    )
                )

            except Exception:
                continue

        return detections

    def recognize_face(self, detection: Detection):
        proc = self.cfg.get("processing", {})
        min_recognition_quality = int(proc.get("min_recognition_quality", 40))

        if detection.quality < min_recognition_quality:
            return None

        if self.active_backend == "onnx":
            return self._recognize_onnx(detection)

        if self.active_backend == "lbph":
            return self._recognize_lbph(detection)

        return None

    def _recognize_onnx(self, detection: Detection):
        if self.embeddings is None or len(self.embedding_labels) == 0:
            return None

        emb = self._embedding(detection.face_bgr)
        if emb is None:
            return None

        try:
            with self.predict_lock:
                scores = self.embeddings @ emb
                best_idx = int(np.argmax(scores))
                score = float(scores[best_idx])
        except Exception:
            return None

        score = max(0.0, min(1.0, score))
        confidence = (1.0 - score) * 100.0

        threshold = float(self.cfg.get("processing", {}).get("onnx_threshold", 0.40))

        label = self.embedding_labels[best_idx]
        identity = self.label_map.get(label)

        if score >= threshold and identity:
            return {
                "person_type": identity[0],
                "person_id": identity[1],
                "confidence": confidence,
                "quality": detection.quality
            }

        return {
            "person_type": "none",
            "person_id": None,
            "confidence": confidence,
            "quality": detection.quality
        }

    def _recognize_lbph(self, detection: Detection):
        if not self.lbph_available or self.model is None:
            return None

        threshold = float(self.cfg.get("processing", {}).get("recognition_threshold", 70))

        face = self._preprocess_lbph_face(detection.face_gray)
        if face is None:
            return None

        try:
            with self.predict_lock:
                label, confidence = self.model.predict(face)
        except Exception:
            return None

        if label == -1:
            return {
                "person_type": "none",
                "person_id": None,
                "confidence": float(confidence),
                "quality": detection.quality
            }

        label = int(label)
        identity = self.label_map.get(label)

        if not identity:
            return {
                "person_type": "none",
                "person_id": None,
                "confidence": float(confidence),
                "quality": detection.quality
            }

        if confidence <= threshold:
            return {
                "person_type": identity[0],
                "person_id": identity[1],
                "confidence": float(confidence),
                "quality": detection.quality
            }

        return {
            "person_type": "none",
            "person_id": None,
            "confidence": float(confidence),
            "quality": detection.quality
        }