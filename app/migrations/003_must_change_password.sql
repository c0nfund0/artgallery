-- Accounts created by an admin get a temporary password that must be replaced on first sign-in.
ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0;
