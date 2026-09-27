-- Site-wide settings editable by admins (previously environment variables).
CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
