import logging

from domain.interfaces import INotificationService
from infrastructure.extensions import db
from domain.models import Alert

logger = logging.getLogger(__name__)


class LoggingNotifier(INotificationService):
    def alert(self, level: str, message: str):
        logger.log(logging.INFO if level != "error" else logging.ERROR, message)

        try:
            db.session.add(Alert(level=level, message=message))
        except Exception:
            logger.exception("خطا در ذخیره هشدار")

    def unknown_created(self, code: str):
        message = f"ناشناس جدید {code} ثبت شد."
        logger.info(message)

        try:
            db.session.add(Alert(level="info", message=message))
        except Exception:
            logger.exception("خطا در ذخیره هشدار ناشناس")