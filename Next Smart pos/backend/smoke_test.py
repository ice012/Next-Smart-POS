from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.seed import reset_db

app = create_app(TestingConfig)

with app.app_context():
    reset_db(app)

client = app.test_client()

# Register a Business Owner
resp = client.post(
    "/api/auth/register",
    json={
        "email": "owner@example.com",
        "password": "Str0ngPass!1",
        "first_name": "Ada",
        "last_name": "Lovelace",
        "business_name": "Analytical Engines Inc",
        "business_country": "GB",
        "business_currency": "GBP",
        "timezone": "Europe/London",
    },
)
print("register", resp.status_code, resp.get_json())
tokens = resp.get_json().get("data", {})
access = tokens.get("access_token")
business_id = resp.get_json()["data"]["business"]["id"]

# Me
resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {access}"})
print("me", resp.status_code, resp.get_json())

# List roles
resp = client.get("/api/iam/roles", headers={"Authorization": f"Bearer {access}"})
print("roles", resp.status_code, resp.get_json())

# Create branch
resp = client.post(
    "/api/business/branches",
    headers={"Authorization": f"Bearer {access}"},
    json={"name": "Manchester Shop", "code": "MANC"},
)
print("branch", resp.status_code, resp.get_json())
