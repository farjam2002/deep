import cv2
from domain.interfaces import ICameraSource


class OpenCVVideoSource(ICameraSource):
    def __init__(self, url: str):
        self.url = url
        self.cap = None
        self.is_webcam = False
        self.camera_index = None

        try:
            self.camera_index = int(url)
            self.is_webcam = True
        except (ValueError, TypeError):
            self.is_webcam = False

    def open(self) -> bool:
        try:
            if self.is_webcam:
                # استفاده از DirectShow برای وبکم
                self.cap = cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW)
                if not self.cap.isOpened():
                    # اگر DSHOW کار نکرد MSMF را امتحان کن
                    self.cap = cv2.VideoCapture(self.camera_index, cv2.CAP_MSMF)
                if not self.cap.isOpened():
                    # اگر هیچکدام کار نکرد پیشفرض
                    self.cap = cv2.VideoCapture(self.camera_index)
                
                # تنظیمات وبکم
                if self.cap.isOpened():
                    self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                    self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                    self.cap.set(cv2.CAP_PROP_FPS, 30)
            else:
                # دوربین شبکه
                self.cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)

            return self.cap.isOpened()
        except Exception:
            return False

    def read(self):
        if not self.cap:
            return False, None
        try:
            ret, frame = self.cap.read()
            if not ret or frame is None:
                return False, None
            return True, frame
        except Exception:
            return False, None

    def release(self):
        try:
            if self.cap:
                self.cap.release()
        except Exception:
            pass

    def is_opened(self) -> bool:
        try:
            return bool(self.cap and self.cap.isOpened())
        except Exception:
            return False
