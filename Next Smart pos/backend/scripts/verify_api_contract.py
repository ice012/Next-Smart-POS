"""End-to-end contract check for the endpoints the frontend consumes."""

import os
import pathlib
import sys
import tempfile

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("FLASK_APP", "wsgi.py")

from app import create_app  # noqa: E402
from app.config import TestingConfig  # noqa: E402

tmp = pathlib.Path(tempfile.gettempdir()) / "nsp_contract_check.sqlite"
if tmp.exists():
    tmp.unlink()


class CheckConfig(TestingConfig):
    SQLALCHEMY_DATABASE_URI = "sqlite:///" + str(tmp).replace("\\", "/")
    SUPERADMIN_API_TOKEN = "contract-token"


failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"PASS  {label}")
    else:
        failures.append(label)
        print(f"FAIL  {label} {detail}")


app = create_app(CheckConfig)
with app.app_context():
    from app.seed import reset_db

    reset_db(app)

client = app.test_client()

registered = client.post(
    "/api/auth/register",
    json={
        "email": "contract@example.com",
        "password": "Str0ngPass!1",
        "first_name": "Ada",
        "last_name": "Lovelace",
        "business_name": "Contract Retail",
        "business_country": "GB",
        "business_currency": "GBP",
        "timezone": "Europe/London",
    },
).get_json()["data"]
headers = {"Authorization": f"Bearer {registered['access_token']}"}

me = client.get("/api/auth/me", headers=headers).get_json()["data"]
check("/api/auth/me exposes module_access", "reports" in me.get("module_access", []), me.get("module_access"))
check("/api/auth/me exposes subscription_status", bool(me.get("subscription_status")), me.get("subscription_status"))

customer = client.post(
    "/api/customers",
    headers=headers,
    json={"display_name": "Contract Customer", "customer_type": "individual"},
).get_json()["data"]["customer"]
item = client.post(
    "/api/items",
    headers=headers,
    json={"type": "product", "name": "Contract Widget", "cost_price": 10, "selling_price": 30, "is_inventory_tracked": False},
).get_json()["data"]["item"]
invoice = client.post(
    "/api/finance/invoices",
    headers=headers,
    json={"customer_id": customer["id"], "items": [{"item_id": item["id"], "description": "Widget", "quantity": 1, "unit_price": 30}]},
).get_json()["data"]["invoice"]
client.post(f"/api/finance/invoices/{invoice['id']}/send", headers=headers)

dashboard = client.get("/api/dashboard", headers=headers)
check("GET /api/dashboard is 200", dashboard.status_code == 200, dashboard.get_json())
data = dashboard.get_json()["data"]
check("dashboard kpi.total_sales is live", data["kpi"]["total_sales"] == 30.0, data["kpi"]["total_sales"])
series = data["charts"]["daily_sales"]
check(
    "dashboard chart rows expose date/label/value",
    all(set(row) == {"date", "label", "value"} for row in series),
    series[:1],
)

for report in ("profit_and_loss", "sales", "expenses", "receivables_aging", "payables_aging", "cash_flow", "inventory"):
    response = client.get(f"/api/reports?report={report}", headers=headers)
    check(f"GET /api/reports?report={report}", response.status_code == 200, response.get_json())
    body = response.get_json()["data"]
    check(f"report {report} has title and summary", bool(body.get("title")) and bool(body.get("summary")), list(body))

csv_response = client.get("/api/reports?report=sales&export=csv", headers=headers)
check("report CSV export", csv_response.status_code == 200 and csv_response.mimetype == "text/csv")
pdf_response = client.get("/api/reports?report=sales&export=pdf", headers=headers)
check("report PDF export", pdf_response.status_code == 200 and pdf_response.data.startswith(b"%PDF"))

sa_headers = {"X-SuperAdmin-Token": "contract-token"}
check("superadmin rejects missing token", client.get("/api/superadmin/stats").status_code == 403)
stats = client.get("/api/superadmin/stats", headers=sa_headers)
check("superadmin stats", stats.status_code == 200, stats.get_json())
check("superadmin counts the tenant", stats.get_json()["data"]["total_businesses"] == 1)
for path in (
    "/api/superadmin/businesses",
    "/api/superadmin/plans",
    "/api/superadmin/modules",
    "/api/superadmin/users",
    "/api/superadmin/audit-logs",
    "/api/superadmin/reports?report=sales",
):
    response = client.get(path, headers=sa_headers)
    check(f"GET {path}", response.status_code == 200, response.get_json())

print()
if failures:
    print(f"{len(failures)} check(s) failed")
    raise SystemExit(1)
print("All contract checks passed")
