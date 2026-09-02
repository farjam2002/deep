from infrastructure.extensions import db
from domain.models import Zone


def seed_defaults() -> None:
    default_zones = [
        "ورودی",
        "خط تولید",
        "انبار",
        "غذاخوری",
        "خروجی"
    ]

    changed = False

    for name in default_zones:
        exists = Zone.query.filter_by(name=name).first()
        if not exists:
            db.session.add(Zone(name=name))
            changed = True

    if changed:
        db.session.commit()