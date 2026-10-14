"use strict";
// Demo backend (Express + PostgreSQL). It is the target DevOS diagnoses and repairs.
// It fails fast with a clear message when its configuration is bad, and keeps serving /health
// even when the database is down, reporting the database state instead of crashing.
const fs = require("fs");
const path = require("path");
const { validateEnv } = require("./config");

function loadDotEnv(file) {
  if (!fs.existsSync(file)) return;
  for (const line of fs.readFileSync(file, "utf8").split(/\r?\n/)) {
    const m = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$/);
    if (m && process.env[m[1]] === undefined) process.env[m[1]] = m[2];
  }
}

function fail(message) {
  console.error(`Error: ${message}`);
  process.exit(1);
}

loadDotEnv(path.join(__dirname, ".env"));

const problems = validateEnv(process.env);
if (problems.length) fail(problems.join("; "));
if (process.env.FORCE_CRASH) throw new Error("FORCE_CRASH is set: simulated startup failure");

const express = require("express"); // throws "Cannot find module 'express'" if node_modules is missing
const { Pool } = require("pg");

const pool = new Pool({ connectionString: process.env.DATABASE_URL, connectionTimeoutMillis: 2000 });
pool.on("error", (e) => console.warn(`database connection problem: ${e.message}`));

const app = express();
app.use(express.json());

app.get("/health", async (_req, res) => {
  let db = "ok";
  try {
    await pool.query("select 1");
  } catch (e) {
    db = "unreachable";
  }
  res.json({ status: "ok", db });
});

app.get("/api/notes", async (_req, res) => {
  try {
    const { rows } = await pool.query("select id, text, created_at from notes order by id desc limit 50");
    res.json(rows);
  } catch (e) {
    res.status(503).json({ error: `database error: ${e.message}` });
  }
});

app.post("/api/notes", async (req, res) => {
  const text = String((req.body && req.body.text) || "").trim();
  if (!text) return res.status(400).json({ error: "text is required" });
  try {
    const { rows } = await pool.query("insert into notes (text) values ($1) returning id, text, created_at", [text]);
    res.status(201).json(rows[0]);
  } catch (e) {
    res.status(503).json({ error: `database error: ${e.message}` });
  }
});

const port = Number(process.env.PORT);
app.listen(port, () => console.log(`demo backend listening on ${port}`));
