from typing import List, Optional

from api import db
from api.models import SubscribeRequest

# For serialization to JSON
class Subscription:
    def __init__(self, email: str, phone: Optional[str] = None, telegram: Optional[str] = None,
                 countries: Optional[List[str]] = None, visa_type: str = "Tourism",
                 consented_at=None):
        self.email = email
        self.phone = phone
        self.telegram = telegram
        self.countries = countries or []
        self.visa_type = visa_type
        self.consented_at = consented_at

    # for serialization to JSON
    def to_dict(self):
        return {
            "email": self.email,
            "phone": self.phone,
            "telegram": self.telegram,
            "countries": self.countries,
            "visa_type": self.visa_type,
            "consented_at": self.consented_at.isoformat() if self.consented_at else None,
        }

    def __str__(self):
        return f"Subscription(email={self.email}, countries={self.countries})"


def find_by_email(email: str) -> Optional[dict]:
    return db.get_user_by_email(email)


def add_subscriber(payload: SubscribeRequest) -> Subscription:
    subscription = Subscription(
        email=payload.email,
        phone=payload.phone,
        telegram=payload.telegram,
        countries=payload.countries,
        visa_type=payload.visa_type,
        consented_at=payload.consented_at,
    )
    user = db.User(
        email=subscription.email,
        phone=subscription.phone,
        telegram=subscription.telegram,
        countries=subscription.countries,
        visa_type=subscription.visa_type,
        consented_at=subscription.consented_at,
    )

    # TODO: if user exists: update user details
    
    db.create_user(user)
    return subscription


def list_subscribers() -> List[dict]:
    return db.list_users()
