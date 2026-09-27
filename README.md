# Atelier — Art Gallery

A self-hosted online art gallery. Visitors browse without an account; artists sign in to upload, edit, publish and unpublish their work.

## Features

**Public (no login)**
- Home page with featured-work mosaic, masonry grid with infinite scroll, sort (newest, most appreciated, most viewed, oldest)
- Artwork page: large view, full-screen lightbox (`f`), ←/→ to move between an artist's works, medium/year/tags, share, "more by this artist"
- Search across title, description, medium, artist and tags; tag and medium filters
- Artist directory and artist profile pages
- Anonymous "appreciate" (like) button, per-session view counter
- Light and dark theme (follows your system setting, can be switched by hand), responsive down to phone width, accessible markup

**Artists (logged in)**
- Drag-and-drop upload of up to 20 images at once, with previews
- Save as private draft or publish immediately; publish/unpublish at any time, one by one or in bulk
- Edit title, description, medium, year and tags; delete
- Studio dashboard with stats (works, published, drafts, views, appreciations)
- Profile (display name, bio, website) and password change (signs out other sessions)

**Admin** (created in the first-run setup, no default accounts exist)
- Gallery settings: name, open or closed artist sign-up
- Manage users (make admin, disable). Disabled artists' work is hidden.
- See, publish/unpublish, edit and feature any artwork on the home page

**Security**
- Passwords hashed with scrypt; CSRF protection on every form; strict Content-Security-Policy (no inline scripts or styles)
- Login rate limiting, protection against open redirects, and password checks that take the same time whether or not the user exists
- Every upload is decoded and re-encoded by Pillow to WebP. This strips EXIF/GPS data, rejects files that aren't images, and guards against decompression bombs.
- Draft images are served only to their owner; published images are served with short cache lifetimes, so unpublishing takes effect quickly
- Container runs as a non-root user on a read-only filesystem with all capabilities dropped

## Stack

Python 3.14 · Flask · SQLite · Pillow · Gunicorn. It uses server-rendered templates and plain CSS/JS. There is **no npm, no frontend build step and no CDN**, so there is less to keep patched.

## Quick start (local)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python scripts/seed_demo.py        # optional: demo artists + generated artwork
flask --app wsgi run --debug       # http://127.0.0.1:5000
```
Demo login: `mira` / `demo-password-123` (admin). Data lives in `./data` (set with `DATA_DIR`).
Skip the seed script to try the real first-run setup; the setup code is printed in the terminal.

Run tests: `pytest -q`

## Production (Docker)

```bash
cp .env.example .env
docker compose up -d
docker compose logs gallery        # shows the one-time setup code
```

### First run
A fresh install has **no user accounts**. Every page redirects to `/setup`, where you enter:
- the **setup code** from the server log (or your own, preset with `SETUP_TOKEN` in `.env`)
- the gallery name, and the admin username and password
- whether artists may sign up themselves

The setup code keeps a stranger from claiming a newly started public server before you do. Once the admin exists, `/setup` is gone for good and the code file is deleted. Sign-ups and the gallery name can be changed later under **Admin → Gallery settings**.

### Persistence
Everything that must survive restarts and updates is in **one directory**, `DATA_DIR`. In Docker this is the `gallery-data` volume mounted at `/data`. The container itself is read-only and disposable.

| Path in `/data` | Contents |
|---|---|
| `gallery.db` (+ `-wal`, `-shm`) | Users, artworks, tags, likes, settings |
| `uploads/` | Image renditions (`<key>_full/medium/thumb.webp`) |
| `secret_key` | Session signing key: 256 random bits from the OS generator, created on first start (unless `SECRET_KEY` is set). Created atomically with mode `0600`. The app refuses to start if it's empty or corrupted. **Back it up**: losing it signs everyone out. |
| `backups/` | Automatic DB snapshot taken before every schema migration |
| `setup_token` | Only until first-run setup completes |

**Back up** by copying the volume, e.g.:
```bash
docker run --rm -v artgallery_gallery-data:/data -v "$PWD":/backup alpine tar czf /backup/gallery-$(date +%F).tgz -C /data .
```
To keep the data in a host folder instead, change the volume to `./data:/data` in `compose.yaml` and run `sudo chown -R 10001:10001 data`. The container runs as UID 10001.
Put a TLS-terminating reverse proxy in front (see `deploy/Caddyfile.example`) and set `SESSION_COOKIE_SECURE=true` and `BEHIND_PROXY=true`.

Recover access (create or reset an admin) from the CLI:
```bash
docker compose exec gallery flask --app wsgi create-admin yourname
```

Back up the `gallery-data` volume. It holds `gallery.db` and `uploads/`.

## Automatic updates & patching

Every layer updates itself:

| Component | How it stays patched |
|---|---|
| Python packages | Pinned in `requirements.txt`. **Dependabot** opens PRs daily. Patch and minor updates **auto-merge** once CI passes on the PR's exact commit (`dependabot-automerge.yml`). Major updates get a comment and wait for review. |
| Base image (Python/Debian) | Dependabot bumps the `FROM` tag. The image is also **rebuilt weekly without cache**, running `apt-get upgrade`, so OS security fixes land even without a tag change. |
| Releases | `release.yml` runs **only after CI passes on `main`**. It builds the image, smoke-tests and scans that exact image, and only then pushes it to `ghcr.io`. A failing test or a fixable HIGH/CRITICAL vulnerability means nothing is published. |
| GitHub Actions | Pinned to commit SHAs (supply-chain safety). Dependabot updates them weekly. |
| Vulnerability detection | CI runs **daily**: `pip-audit` on Python deps and **Trivy** on the built image. It fails on fixable HIGH/CRITICAL issues. |
| Database schema | Versioned migrations in `app/migrations/NNN_*.sql` are applied automatically on startup. Each runs in a transaction, and a DB backup is taken first. |
| Running server | `deploy/auto-update.sh` pulls the latest image, restarts, waits for `/healthz`, and **rolls back automatically** if the new version is unhealthy. Install the systemd timer to run it every 6 hours: |

```bash
sudo cp deploy/artgallery-update.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now artgallery-update.timer
```
For the host OS, enable `unattended-upgrades` (Debian/Ubuntu).

**Setup notes:** auto-merge is done by a workflow instead of GitHub's built-in auto-merge, because branch protection isn't available for private repos on the free plan. Because the repo is private, the image is private too. Log the server in once with a token that has `read:packages`: `echo <token> | docker login ghcr.io -u c0nfund0 --password-stdin`.

### Changing the database schema
Add a new file such as `app/migrations/002_add_collections.sql`. It runs once on the next start.

## Configuration

| Variable | Default | |
|---|---|---|
| `SETUP_TOKEN` | random, printed to log | First-run setup code |
| `SECRET_KEY` | auto-generated in data dir | Session signing key; if set, must be 32+ characters |
| `MAX_UPLOAD_MB` | `25` | Max request size |
| `DATA_DIR` | `./data` (`/data` in Docker) | All persistent state |
| `SESSION_COOKIE_SECURE` | `false` | Set `true` behind HTTPS |
| `BEHIND_PROXY` | `false` | Trust `X-Forwarded-*` headers |
| `WEB_CONCURRENCY` | `2×CPU (max 4)` | Gunicorn workers |

## Project layout
```
app/
  __init__.py      app factory, error pages, /healthz
  db.py            SQLite + automatic migrations, CLI commands
  security.py      auth helpers, CSRF, security headers, rate limiting
  images.py        upload validation + WebP renditions
  models.py        queries (search, tags, permissions)
  gallery.py       public views      studio.py   artist views
  auth.py          login/register    admin.py    admin views
  setup.py         first-run admin setup         settings.py  DB-backed site settings
  migrations/      NNN_*.sql
  templates/  static/
deploy/            auto-update script, systemd units, Caddy example
tests/             pytest suite
```
