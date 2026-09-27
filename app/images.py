"""Image validation and processing.

Every upload is decoded and re-encoded by Pillow: this strips metadata (EXIF/GPS),
neutralises polyglot files, and produces web-friendly WebP renditions.

Resource limits (an unattended server must survive hostile uploads):
- Pixel count is capped *before* decoding. A 400 KB PNG can decode to 150 MP
  and take ~1 GB of RAM; the cap keeps a single decode under ~200 MB.
- Renditions are derived from each other (full -> medium -> thumb) so the
  full-size decode is the only large buffer, and it is released early.
- Only a bounded number of decodes run at once per process.
"""
import secrets
import threading
import warnings
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

# JPEG/PNG/WebP/GIF cover what artists upload; TIFF and BMP decoders have the
# worst security track record and are rarely needed on the web.
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "GIF"}
MAX_PIXELS = 40_000_000  # 40 MP, e.g. 8000 x 5000
MIN_SIDE = 64

# Pillow raises above 2x this value at open time (cheapest rejection); anything
# between MAX_PIXELS and 2x is rejected by our explicit check below.
Image.MAX_IMAGE_PIXELS = MAX_PIXELS
warnings.simplefilter("ignore", Image.DecompressionBombWarning)

# Ordered largest -> smallest; each rendition is resized from the previous one.
RENDITIONS = {
    "full": 2400,   # detail/lightbox view
    "medium": 1200, # detail page
    "thumb": 640,   # grids
}
_QUALITY = {"full": 86, "medium": 86, "thumb": 80}

# At most this many concurrent decodes per worker process.
_decode_slots = threading.BoundedSemaphore(1)
_SLOT_WAIT_SECONDS = 30


class ImageError(ValueError):
    pass


def process_upload(stream, upload_dir: str) -> dict:
    """Validate an uploaded file and write all renditions. Returns metadata."""
    if not _decode_slots.acquire(timeout=_SLOT_WAIT_SECONDS):
        raise ImageError("The server is busy processing other uploads. Please try again in a moment.")
    try:
        return _process(stream, upload_dir)
    finally:
        _decode_slots.release()


def _process(stream, upload_dir: str) -> dict:
    try:
        img = Image.open(stream)  # reads the header only
        fmt = img.format
        width, height = img.size
        if fmt not in ALLOWED_FORMATS:
            raise ImageError("Unsupported image format. Use JPEG, PNG, WebP or GIF.")
        if width * height > MAX_PIXELS:
            raise ImageError(
                f"Image is too large ({width} × {height} px). "
                f"The maximum is {MAX_PIXELS // 1_000_000} megapixels; please downscale it first."
            )
        if min(width, height) < MIN_SIDE:
            raise ImageError(f"Image is too small (minimum {MIN_SIDE} px on each side).")
        img.verify()  # structural check; the image must be reopened afterwards
        stream.seek(0)
        img = Image.open(stream)
        if fmt == "JPEG":
            # Let libjpeg decode at 1/2, 1/4 or 1/8 scale when the source is far
            # larger than the biggest rendition: much less memory and time.
            img.draft(None, (RENDITIONS["full"], RENDITIONS["full"]))
        img.load()
    except ImageError:
        raise
    except Image.DecompressionBombError as e:
        raise ImageError("That image is too large to process.") from e
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as e:
        raise ImageError("That file doesn't look like a valid image.") from e

    img = ImageOps.exif_transpose(img)
    if (img.width > img.height) != (width > height):
        width, height = height, width  # EXIF orientation rotated the image 90°
    has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    img = img.convert("RGBA" if has_alpha else "RGB")

    key = secrets.token_urlsafe(16)
    out = Path(upload_dir)
    color = None
    current = img
    del img  # `current` is now the only reference to the full-size decode
    for name, max_side in RENDITIONS.items():
        if max(current.size) > max_side:
            scale = max_side / max(current.size)
            size = (max(1, round(current.width * scale)), max(1, round(current.height * scale)))
            current = current.resize(size, Image.Resampling.LANCZOS, reducing_gap=3.0)
        if color is None:
            color = dominant_color(current)
        current.save(out / f"{key}_{name}.webp", "WEBP", quality=_QUALITY[name], method=5)

    return {"image_key": key, "width": width, "height": height, "color": color}


def dominant_color(img: Image.Image) -> str:
    small = img.convert("RGB").resize((1, 1), Image.Resampling.BOX)
    r, g, b = small.getpixel((0, 0))
    return f"#{r:02x}{g:02x}{b:02x}"


def delete_renditions(key: str, upload_dir: str) -> None:
    for name in RENDITIONS:
        (Path(upload_dir) / f"{key}_{name}.webp").unlink(missing_ok=True)
