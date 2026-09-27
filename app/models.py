"""Query helpers shared by the blueprints."""
import re

from .db import get_db

ARTWORK_SELECT = """
SELECT a.*, u.username, u.display_name, u.is_active AS artist_active,
       (SELECT COUNT(*) FROM likes l WHERE l.artwork_id = a.id) AS like_count
FROM artworks a JOIN users u ON u.id = a.user_id
"""

SORTS = {
    "new": "COALESCE(a.published_at, a.created_at) DESC, a.id DESC",
    "popular": "like_count DESC, a.views DESC, a.id DESC",
    "views": "a.views DESC, a.id DESC",
    "old": "COALESCE(a.published_at, a.created_at) ASC, a.id ASC",
}


def normalize_tags(raw: str) -> list[str]:
    tags = []
    for t in re.split(r"[,#]", raw or ""):
        t = re.sub(r"\s+", " ", t).strip().lower()[:40]
        if t and t not in tags:
            tags.append(t)
    return tags[:15]


def set_tags(artwork_id: int, tags: list[str]) -> None:
    db = get_db()
    db.execute("DELETE FROM artwork_tags WHERE artwork_id = ?", (artwork_id,))
    for name in tags:
        db.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
        tag_id = db.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()["id"]
        db.execute("INSERT OR IGNORE INTO artwork_tags VALUES (?, ?)", (artwork_id, tag_id))
    db.execute("DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM artwork_tags)")


def tags_for(artwork_ids: list[int]) -> dict[int, list[str]]:
    if not artwork_ids:
        return {}
    marks = ",".join("?" * len(artwork_ids))
    rows = get_db().execute(
        f"""SELECT at.artwork_id, t.name FROM artwork_tags at
            JOIN tags t ON t.id = at.tag_id
            WHERE at.artwork_id IN ({marks}) ORDER BY t.name""",
        artwork_ids,
    ).fetchall()
    out: dict[int, list[str]] = {i: [] for i in artwork_ids}
    for r in rows:
        out[r["artwork_id"]].append(r["name"])
    return out


def search_artworks(
    *, q="", tag="", user_id=None, medium="", sort="new", published_only=True,
    featured=False, page=1, per_page=24,
):
    where, params = [], []
    if published_only:
        where.append("a.is_published = 1 AND u.is_active = 1")
    if featured:
        where.append("a.is_featured = 1")
    if user_id is not None:
        where.append("a.user_id = ?")
        params.append(user_id)
    if medium:
        where.append("a.medium = ? COLLATE NOCASE")
        params.append(medium)
    if tag:
        where.append(
            "a.id IN (SELECT at.artwork_id FROM artwork_tags at JOIN tags t ON t.id = at.tag_id WHERE t.name = ?)"
        )
        params.append(tag.lower())
    if q:
        like = f"%{escape_like(q)}%"
        where.append(
            """(a.title LIKE ? ESCAPE '\\' OR a.description LIKE ? ESCAPE '\\'
                OR a.medium LIKE ? ESCAPE '\\' OR u.display_name LIKE ? ESCAPE '\\'
                OR a.id IN (SELECT at.artwork_id FROM artwork_tags at JOIN tags t ON t.id = at.tag_id
                            WHERE t.name LIKE ? ESCAPE '\\'))"""
        )
        params += [like] * 5
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    order = SORTS.get(sort, SORTS["new"])
    db = get_db()
    total = db.execute(
        f"SELECT COUNT(*) FROM artworks a JOIN users u ON u.id = a.user_id {clause}", params
    ).fetchone()[0]
    rows = db.execute(
        f"{ARTWORK_SELECT} {clause} ORDER BY {order} LIMIT ? OFFSET ?",
        params + [per_page, (page - 1) * per_page],
    ).fetchall()
    return rows, total


def escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def get_artwork(artwork_id: int):
    return get_db().execute(f"{ARTWORK_SELECT} WHERE a.id = ?", (artwork_id,)).fetchone()


def popular_tags(limit=20):
    return get_db().execute(
        """SELECT t.name, COUNT(*) AS n FROM tags t
           JOIN artwork_tags at ON at.tag_id = t.id
           JOIN artworks a ON a.id = at.artwork_id AND a.is_published = 1
           GROUP BY t.id ORDER BY n DESC, t.name LIMIT ?""",
        (limit,),
    ).fetchall()


def can_manage(user, artwork) -> bool:
    return user is not None and (user["is_admin"] or user["id"] == artwork["user_id"])


def can_view(user, artwork) -> bool:
    return bool(artwork["is_published"] and artwork["artist_active"]) or can_manage(user, artwork)
