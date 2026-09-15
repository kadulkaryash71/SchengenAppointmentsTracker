import os
import uuid
from typing import List, Optional

from dotenv import load_dotenv
from pymongo import MongoClient

# file for MongoDB setup
load_dotenv()  # Load environment variables from .env file

client = MongoClient(os.getenv("DB_URI", "mongodb://localhost:27017/"))
db = client[os.getenv("DB_NAME", "schengen_slot_bot")]
users_collection = db[os.getenv("DB_USER_COLLECTION", "users")]
users_collection.create_index("email", unique=True)


class User:
    def __init__(self, email, phone=None, telegram=None, countries=None,
                 visa_type="Tourism", consented_at=None, user_id=None):
        self.user_id = user_id or uuid.uuid4().hex
        self.email = email
        self.phone = phone
        self.telegram = telegram
        self.countries = countries or []
        self.visa_type = visa_type
        self.consented_at = consented_at

    def to_dict(self):
        return {
            "_id": self.user_id,
            "email": self.email,
            "phone": self.phone,
            "telegram": self.telegram,
            "countries": self.countries,
            "visa_type": self.visa_type,
            "consented_at": self.consented_at.isoformat() if hasattr(self.consented_at, "isoformat") else self.consented_at,
        }


def create_user(user: User) -> User:
    users_collection.insert_one(user.to_dict())
    return user


def get_user_by_email(email: str) -> Optional[dict]:
    return users_collection.find_one({"email": email})


def list_users() -> List[dict]:
    return list(users_collection.find())

def get_mailing_list() -> List[str]:
    return [user["email"] for user in users_collection.find({}, {"email": 1, "_id": 0})]