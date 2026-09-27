"""Fill a local instance with generated demo artwork: python scripts/seed_demo.py"""
import io
import math
import random
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import create_app  # noqa: E402

PALETTES = [
    ["#1d3557", "#457b9d", "#a8dadc", "#f1faee", "#e63946"],
    ["#264653", "#2a9d8f", "#e9c46a", "#f4a261", "#e76f51"],
    ["#0b090a", "#161a1d", "#660708", "#a4161a", "#e5383b"],
    ["#f6bd60", "#f7ede2", "#f5cac3", "#84a59d", "#f28482"],
    ["#003049", "#d62828", "#f77f00", "#fcbf49", "#eae2b7"],
    ["#10002b", "#3c096c", "#7b2cbf", "#c77dff", "#e0aaff"],
    ["#283618", "#606c38", "#fefae0", "#dda15e", "#bc6c25"],
]
TITLES = ["Northern Hours", "Salt and Ember", "Quiet Harbour", "After the Rain", "Field Notes", "Vesper",
          "Low Sun", "Tidal Memory", "Red Room", "Midsummer", "Lanterns", "Soft Geometry", "The Long Winter",
          "Orchard", "Glasshouse", "Drift", "Meridian", "Copper Sky"]
MEDIUMS = ["Oil on canvas", "Acrylic", "Digital painting", "Watercolour", "Mixed media", "Printmaking"]
TAGS = ["abstract", "landscape", "nordic", "light", "colour field", "geometric", "minimal", "sea", "night", "warm"]


def art(seed, w, h):
    rnd = random.Random(seed)
    pal = rnd.choice(PALETTES)
    img = Image.new("RGB", (w, h), pal[0])
    d = ImageDraw.Draw(img)
    style = seed % 3
    if style == 0:  # horizon bands
        y = 0
        while y < h:
            bh = rnd.randint(h // 12, h // 3)
            d.rectangle([0, y, w, y + bh], fill=rnd.choice(pal))
            y += bh
        img = img.filter(ImageFilter.GaussianBlur(w // 40))
        d = ImageDraw.Draw(img)
        r = rnd.randint(w // 10, w // 5)
        cx, cy = rnd.randint(r, w - r), rnd.randint(r, h // 2)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=pal[-1])
    elif style == 1:  # circles
        for _ in range(rnd.randint(6, 14)):
            r = rnd.randint(w // 12, w // 3)
            cx, cy = rnd.randint(0, w), rnd.randint(0, h)
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=rnd.choice(pal[1:]))
        img = img.filter(ImageFilter.GaussianBlur(2))
    else:  # waves
        for i in range(24):
            col = pal[i % len(pal)]
            amp, freq, off = rnd.randint(10, h // 8), rnd.uniform(1, 4), h * i / 24
            pts = [(x, off + amp * math.sin(x / w * freq * math.tau + i)) for x in range(0, w + 10, 10)]
            d.polygon(pts + [(w, h), (0, h)], fill=col)
    grain = Image.effect_noise((w, h), 18).convert("RGB")
    return Image.blend(img, grain, 0.06)


def main():
    app = create_app({"CSRF_DISABLED": True, "TESTING": True})
    c = app.test_client()
    artists = [("mira", "Mira Kallio"), ("jonas", "Jonas Berg"), ("aino", "Aino Laine")]
    n = 0
    for username, name in artists:
        pw = "demo-password-123"
        c.post("/register", data={"username": username, "display_name": name, "password": pw, "password2": pw})
        c.post("/login", data={"username": username, "password": pw})
        c.post("/account", data={"action": "profile", "display_name": name,
                                 "bio": f"{name.split()[0]} works between colour and silence, painting the northern light.",
                                 "website": ""})
        for _ in range(6):
            w, h = random.choice([(900, 1200), (1200, 900), (1000, 1000), (800, 1300), (1400, 800)])
            buf = io.BytesIO()
            art(n, w, h).save(buf, "JPEG", quality=90)
            buf.seek(0)
            data = {"images": (buf, "a.jpg"), "title": TITLES[n % len(TITLES)], "medium": random.choice(MEDIUMS),
                    "year": str(random.randint(2015, 2026)), "tags": ", ".join(random.sample(TAGS, 3)),
                    "description": "An exploration of how colour holds memory.\nPainted over one long season."}
            if n % 7 != 6:
                data["publish"] = "1"
            c.post("/studio/upload", data=data, content_type="multipart/form-data")
            n += 1
        c.post("/logout")
    # Feature a few as admin (first user) and add some likes.
    c.post("/login", data={"username": "mira", "password": "demo-password-123"})
    html = c.get("/admin/").get_data(as_text=True)
    for aid in re.findall(r'/admin/art/(\d+)/feature', html)[:4]:
        c.post(f"/admin/art/{aid}/feature")
    for i in range(1, n + 1):
        for _ in range(random.randint(0, 5)):
            app.test_client().post(f"/art/{i}/like")
    print(f"Seeded {n} artworks for {len(artists)} artists. Log in as mira / demo-password-123 (admin).")


if __name__ == "__main__":
    main()
