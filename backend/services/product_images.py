from io import BytesIO
import os
from pathlib import Path
import secrets
import tempfile
from urllib.parse import urlsplit

from flask import current_app, url_for
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.datastructures import FileStorage

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_PRODUCT_IMAGES = 5
ALLOWED_FORMATS = {
    "JPEG": ("jpg", "JPEG"),
    "PNG": ("png", "PNG"),
    "WEBP": ("webp", "WEBP"),
}


class InvalidProductImage(ValueError):
    pass


def image_source(image_path):
    if not image_path:
        return ""
    try:
        parsed = urlsplit(image_path)
    except ValueError:
        return ""
    if parsed.scheme or parsed.netloc:
        if (
            parsed.scheme.lower() in {"http", "https"}
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
        ):
            return image_path
        return ""

    relative_path = image_path.lstrip("/")
    if relative_path.startswith("static/"):
        relative_path = relative_path[len("static/") :]
    if not relative_path or "\\" in relative_path:
        return ""
    parts = Path(relative_path).parts
    if any(part in {"", ".", ".."} for part in parts):
        return ""
    return url_for("static", filename=relative_path)


def prepare_image(upload):
    if not isinstance(upload, FileStorage):
        raise InvalidProductImage("Tệp ảnh tải lên không hợp lệ.")
    content = upload.stream.read(MAX_IMAGE_BYTES + 1)
    if len(content) > MAX_IMAGE_BYTES:
        raise InvalidProductImage("Mỗi ảnh không được vượt quá 5 MB.")
    if not content:
        raise InvalidProductImage("Tệp ảnh bị trống.")

    try:
        with Image.open(BytesIO(content)) as probe:
            image_format = probe.format
            probe.verify()
        if image_format not in ALLOWED_FORMATS:
            raise InvalidProductImage("Chỉ chấp nhận ảnh JPG, JPEG, PNG hoặc WebP.")

        with Image.open(BytesIO(content)) as image:
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise InvalidProductImage("Kích thước ảnh vượt quá giới hạn xử lý an toàn.")
            image = ImageOps.exif_transpose(image)
            image.load()
            extension, output_format = ALLOWED_FORMATS[image_format]
            if output_format == "JPEG":
                if image.mode not in {"RGB", "L"}:
                    image = image.convert("RGB")
            elif output_format == "PNG" and image.mode not in {
                "1",
                "L",
                "LA",
                "P",
                "RGB",
                "RGBA",
                "I",
                "F",
            }:
                image = image.convert("RGBA")
            elif output_format == "WEBP" and image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGBA" if "A" in image.getbands() else "RGB")

            output = BytesIO()
            save_options = {"optimize": True}
            if output_format in {"JPEG", "WEBP"}:
                save_options["quality"] = 90
            if output_format == "WEBP":
                save_options["method"] = 6
            image.save(output, format=output_format, **save_options)
    except InvalidProductImage:
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        OSError,
        UnidentifiedImageError,
        ValueError,
    ) as error:
        raise InvalidProductImage(
            "Tệp không phải ảnh hợp lệ hoặc không thể xử lý."
        ) from error

    normalized = output.getvalue()
    if len(normalized) > MAX_IMAGE_BYTES:
        raise InvalidProductImage("Ảnh sau khi chuẩn hóa vượt quá giới hạn 5 MB.")
    return extension, normalized


def upload_directory():
    return Path(current_app.static_folder).resolve() / "uploads" / "products"


def write_pending_image(extension, content):
    directory = upload_directory()
    directory.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".pending-", suffix=".tmp", dir=directory
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(content)
        filename = f"{secrets.token_hex(16)}.{extension}"
        final_path = directory / filename
        relative_path = Path("uploads") / "products" / filename
        return temporary_path, final_path, relative_path.as_posix()
    except OSError:
        temporary_path.unlink(missing_ok=True)
        raise


def is_managed_upload(image_path):
    if not image_path or image_path.startswith(("http://", "https://")):
        return False
    relative_path = Path(image_path)
    if relative_path.is_absolute() or "\\" in image_path:
        return False
    if any(part in {"", ".", ".."} for part in relative_path.parts):
        return False
    root = upload_directory()
    try:
        resolved = (Path(current_app.static_folder).resolve() / relative_path).resolve()
        resolved.relative_to(root)
        return True
    except (OSError, ValueError):
        return False


def remove_managed_upload(image_path):
    if not is_managed_upload(image_path):
        return False
    path = (Path(current_app.static_folder).resolve() / image_path).resolve()
    path.unlink(missing_ok=True)
    return True
