"""Shared helpers for user-supplied image uploads.

Extensions, size limits and stored filenames are all decided server side so a
crafted filename can never escape the upload directory.
"""

import os
import uuid as uuidlib
from pathlib import Path

from flask import current_app, url_for

from ..exceptions import ValidationError


def save_image(file_storage, subdir, *, field_label="image", max_bytes_key="IMAGE_MAX_BYTES"):
    """Validate and persist an uploaded image, returning its public URL.

    `subdir` namespaces the file inside the upload folder so logos and product
    images cannot collide.
    """
    if not file_storage or not file_storage.filename:
        raise ValidationError(f"A {field_label} file is required")

    ext = Path(file_storage.filename).suffix.lower()
    allowed = current_app.config["ALLOWED_IMAGE_EXTENSIONS"]
    if ext not in allowed:
        raise ValidationError(
            f"Unsupported {field_label} type. Allowed: {', '.join(sorted(allowed))}"
        )

    max_bytes = current_app.config[max_bytes_key]
    file_storage.stream.seek(0, os.SEEK_END)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if size == 0:
        raise ValidationError(f"The uploaded {field_label} is empty")
    if size > max_bytes:
        raise ValidationError(
            f"{field_label.capitalize()} must be smaller than {max_bytes // (1024 * 1024)}MB"
        )

    upload_dir = Path(current_app.config["UPLOAD_FOLDER"]) / subdir
    upload_dir.mkdir(parents=True, exist_ok=True)

    # Generated name: never trust the client's filename for the stored path.
    stored_name = f"{uuidlib.uuid4().hex}{ext}"
    file_storage.save(upload_dir / stored_name)
    return url_for("uploaded_file", filename=f"{subdir}/{stored_name}", _external=True)


def delete_image(url, subdir):
    """Best-effort removal of a previously uploaded file.

    Missing files are ignored so a stale URL never breaks the request that is
    clearing it.
    """
    if not url:
        return
    marker = f"/uploads/{subdir}/"
    if marker not in url:
        return
    name = url.split(marker, 1)[1].split("?", 1)[0]
    if not name or "/" in name:
        return
    target = Path(current_app.config["UPLOAD_FOLDER"]) / subdir / name
    try:
        target.unlink()
    except OSError:
        pass
