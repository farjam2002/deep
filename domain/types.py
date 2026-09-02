class Detection:
    def __init__(self, bbox, face_gray, face_bgr, hist, quality):
        self.bbox = bbox
        self.face_gray = face_gray
        self.face_bgr = face_bgr
        self.hist = hist
        self.quality = quality