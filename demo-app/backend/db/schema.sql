-- Demo app schema. Idempotent: safe to run more than once.
CREATE TABLE IF NOT EXISTS notes (
  id         SERIAL PRIMARY KEY,
  text       TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO notes (text)
SELECT 'Welcome to the DevOS demo app'
WHERE NOT EXISTS (SELECT 1 FROM notes);
