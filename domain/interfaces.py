from abc import ABC, abstractmethod


class IFaceEngine(ABC):
    @abstractmethod
    def mark_dirty(self):
        pass

    @abstractmethod
    def should_train(self) -> bool:
        pass

    @abstractmethod
    def train(self):
        pass

    @abstractmethod
    def detect_faces(self, bgr_frame, gray_frame, mask=None):
        pass

    @abstractmethod
    def recognize_face(self, detection):
        pass


class ICameraSource(ABC):
    @abstractmethod
    def open(self) -> bool:
        pass

    @abstractmethod
    def read(self):
        pass

    @abstractmethod
    def release(self):
        pass

    @abstractmethod
    def is_opened(self) -> bool:
        pass


class ITracker(ABC):
    @property
    @abstractmethod
    def tracks(self):
        pass

    @abstractmethod
    def update(self, detections, now, frame_shape):
        pass

    @abstractmethod
    def has_active(self) -> bool:
        pass


class IStorage(ABC):
    @abstractmethod
    def save_uploaded_file(self, category: str, file, filename: str) -> str:
        pass

    @abstractmethod
    def save_cv_image(self, category: str, filename: str, image) -> str:
        pass

    @abstractmethod
    def copy_file(self, category: str, src_path: str, filename: str) -> str:
        pass

    @abstractmethod
    def delete(self, path: str):
        pass

    @abstractmethod
    def exists(self, path: str) -> bool:
        pass


class INotificationService(ABC):
    @abstractmethod
    def alert(self, level: str, message: str):
        pass

    @abstractmethod
    def unknown_created(self, code: str):
        pass