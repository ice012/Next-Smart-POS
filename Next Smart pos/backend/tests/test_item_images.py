"""Tests for product image upload and removal."""

import io

from conftest import auth_headers

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def create_item(client, headers, **overrides):
    body = {
        "type": "product",
        "name": overrides.pop("name", "Blue Mug"),
        "cost_price": "4.50",
        "selling_price": "12.00",
    }
    body.update(overrides)
    response = client.post("/api/items", json=body, headers=headers)
    assert response.status_code == 201, response.get_json()
    return response.get_json()["data"]["item"]


def test_new_item_has_null_image(owner):
    item = create_item(owner["client"], owner["headers"])
    assert item["image_url"] is None


def test_upload_item_image(owner):
    client, headers = owner["client"], owner["headers"]
    item = create_item(client, headers)

    response = client.post(
        f"/api/items/{item['id']}/image",
        data={"image": (io.BytesIO(PNG), "mug.png")},
        headers=headers,
        content_type="multipart/form-data",
    )
    assert response.status_code == 200, response.get_json()
    image_url = response.get_json()["data"]["image_url"]
    assert image_url and "/uploads/items/" in image_url

    fetched = client.get(f"/api/items/{item['id']}", headers=headers)
    assert fetched.get_json()["data"]["item"]["image_url"] == image_url


def test_replacing_image_removes_the_previous_file(owner, app):
    from pathlib import Path

    client, headers = owner["client"], owner["headers"]
    item = create_item(client, headers)

    def upload(name):
        return client.post(
            f"/api/items/{item['id']}/image",
            data={"image": (io.BytesIO(PNG), name)},
            headers=headers,
            content_type="multipart/form-data",
        )

    first = upload("first.png").get_json()["data"]["image_url"]
    second = upload("second.png").get_json()["data"]["image_url"]
    assert first != second

    folder = Path(app.config["UPLOAD_FOLDER"], "items")
    first_file = folder / first.rsplit("/", 1)[1]
    second_file = folder / second.rsplit("/", 1)[1]
    assert not first_file.exists(), "the superseded image should have been unlinked"
    assert second_file.exists(), "the current image should still be on disk"


def test_delete_item_image(owner):
    client, headers = owner["client"], owner["headers"]
    item = create_item(client, headers)
    client.post(
        f"/api/items/{item['id']}/image",
        data={"image": (io.BytesIO(PNG), "mug.png")},
        headers=headers,
        content_type="multipart/form-data",
    )

    removed = client.delete(f"/api/items/{item['id']}/image", headers=headers)
    assert removed.status_code == 200, removed.get_json()
    assert removed.get_json()["data"]["image_url"] is None

    fetched = client.get(f"/api/items/{item['id']}", headers=headers)
    assert fetched.get_json()["data"]["item"]["image_url"] is None


def test_rejects_non_image_upload(owner):
    item = create_item(owner["client"], owner["headers"])
    response = owner["client"].post(
        f"/api/items/{item['id']}/image",
        data={"image": (io.BytesIO(b"nope"), "payload.exe")},
        headers=owner["headers"],
        content_type="multipart/form-data",
    )
    assert response.status_code == 422


def test_rejects_oversized_upload(owner):
    item = create_item(owner["client"], owner["headers"])
    oversized = b"\x89PNG\r\n\x1a\n" + b"0" * (3 * 1024 * 1024)
    response = owner["client"].post(
        f"/api/items/{item['id']}/image",
        data={"image": (io.BytesIO(oversized), "big.png")},
        headers=owner["headers"],
        content_type="multipart/form-data",
    )
    assert response.status_code in (413, 422)


def test_image_upload_requires_authentication(client):
    response = client.post(
        "/api/items/00000000-0000-0000-0000-000000000000/image",
        data={"image": (io.BytesIO(PNG), "mug.png")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 401


def test_image_cannot_be_attached_to_another_tenants_item(owner, client):
    from conftest import register_business

    other = register_business(client, email="other@example.com", business_name="Other Ltd")
    other_headers = auth_headers(other["access_token"])
    other_item = create_item(client, other_headers, name="Theirs")

    # The item exists but is not owned by the caller's business.
    response = client.get(f"/api/items/{other_item['id']}", headers=owner["headers"])
    assert response.status_code == 404

    upload = client.post(
        f"/api/items/{other_item['id']}/image",
        data={"image": (io.BytesIO(PNG), "theft.png")},
        headers=owner["headers"],
        content_type="multipart/form-data",
    )
    assert upload.status_code == 404


def test_cors_preflight_for_image_endpoints(owner):
    client = owner["client"]
    for method in ("POST", "DELETE"):
        response = client.options(
            f"/api/items/00000000-0000-0000-0000-000000000000/image",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert response.status_code == 200, f"preflight failed for {method}"
        assert (
            response.headers.get("Access-Control-Allow-Origin")
            == "http://localhost:5173"
        )
