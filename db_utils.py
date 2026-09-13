import uuid
import pymongo



class User:
    def __init__(self, email: str):
        self.user_id = uuid.uuid4().hex
        self.email = email

    def update_email(self, new_email: str):
        self.email = new_email

    # for serialization (MongoDB) to dict
    def to_dict(self):
        return {
            "user_id": self.user_id,
            "email": self.email
        }

    def __str__(self):
        return f"User(user_id={self.user_id}, email={self.email})"


class Appointment:
    def __init__(self, code: str, country: str, status: str, earliest: str, slots: int):
        self.code = code
        self.country = country
        self.status = status
        self.earliest = earliest
        self.slots = slots

    # update appointment status
    def update_appointment(self, status: str, earliest: str, slots: int):
        self.status = status
        self.earliest = earliest
        self.slots = slots

    # for serialization (MongoDB) to dict
    def to_dict(self):
        return {
            "code": self.code,
            "country": self.country,
            "status": self.status,
            "earliest": self.earliest,
            "slots": self.slots
        }

    # read appointment details
    def __str__(self):
        return f"Appointment(code={self.code}, country={self.country}, status={self.status}, earliest={self.earliest}, slots={self.slots})"


class UserDatabase:
    def __init__(self, uri: str, db_name: str):
        self.client = pymongo.MongoClient(uri)
        self.db = self.client[db_name]
        self.user_collection = self.db["users"]

    def add_user(self, email: str):
        user_data = {
            "user_id": uuid.uuid4().hex,
            "email": email
        }
        self.user_collection.insert_one(user_data)

    def get_user(self, email: str):
        return self.user_collection.find_one({"email": email})

    def update_subscription(self, email: str, new_email: str):
        self.user_collection.update_one(
            {"email": email},
            {"$set": {"email": new_email}}
        )

    def delete_user(self, email: str):
        self.user_collection.delete_one({"email": email})