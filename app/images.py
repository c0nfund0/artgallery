"""Image validation and processing.

Every upload is decoded and re-encoded by Pillow: this strips metadata (EXIF/GPS),
neutralises polyglot files, and produces web-friendly WebP renditions.
"""
import secrets
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "GIF", "TIFF", "BMP"}
Image.MAX_IMAGE_PIXELS = 80_000_000  # guard against decompression bombs

RENDITIONS = {
    "full": 2400,   # detail/lightbox view
    "medium": 1200, # detail page
    "thumb": 640,   # grids
}


class ImageError(ValueError):
    pass


def process_upload(stream, upload_dir: str) -> dict:
    """Validate an uploaded file and write all renditions. Returns metadata."""
    try:
        img = Image.open(stream)
        img.verify()  # structural check
        stream.seek(0)
        img = Image.open(stream)
        if img.format not in ALLOWED_FORMATS:
            raise ImageError("Unsupported image format. Use JPEG, PNG, WebP, GIF or TIFF.")
        img.load()
    except (UnidentifiedImageError, OSError, SyntaxError) as e:
        raise ImageError("That file doesn't look like a valid image.") from e
    except Image.DecompressionBombError as e:
        raise ImageError("That image is too large to process.") from e

    img = ImageOps.exif_transpose(img)
    has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    img = img.convert("RGBA" if has_alpha else "RGB")

    if min(img.size) < 64:
        raise ImageError("Image is too small (minimum 64 px on each side).")

    key = secrets.token_urlsafe(16)
    out = Path(upload_dir)
    for name, max_side in RENDITIONS.items():
        r = img.copy()
        r.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        r.save(out / f"{key}_{name}.webp", "WEBP", quality=86 if name != "thumb" else 80, method=5)

    return {
        "image_key": key,
        "width": img.width,
        "height": img.height,
        "color": dominant_color(img),
    }


def dominant_color(img: Image.Image) -> str:
    small = img.convert("RGB").resize((1, 1), Image.Resampling.BOX)
    r, g, b = small.getpixel((0, 0))
    return f"#{r:02x}{g:02x}{b:02x}"


def delete_renditions(key: str, upload_dir: str) -> None:
    for name in RENDITIONS:
        (Path(upload_dir) / f"{key}_{name}.webp").unlink(missing_ok=True)
