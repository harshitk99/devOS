"use strict";
// Sandbox runner. Receives ONE typed request as JSON in $DEVOS_PAYLOAD and dispatches to a fixed
// handler. There is no code path that executes a caller-supplied command line. The only processes
// this file spawns are fixed npm command lines written below (`npm start`, `npm install
// --ignore-scripts`); parameters never become arguments, so a package name or script cannot be
// smuggled in.
//
//   {"mode": "action", "action": {"type": "set_env_var", "params": {...}}}
//   {"mode": "probe"}   start the app, see whether it stays up
//   {"mode": "verify"}  probe, plus the project's build/test scripts
//
// The last stdout line is always `DEVOS_RESULT:<json>`.
const fs = require("fs");
const http = require("http");
const path = require("path");
const { spawn } = require("child_process");

const NPM_ENV = { HOME: "/tmp", npm_config_cache: "/tmp/npm-cache", npm_config_update_notifier: "false" };

/** Run a fixed command line to completion (bounded by `timeoutMs`); never a caller-supplied one. */
function runFixed(cmd, args, timeoutMs) {
  return new Promise((resolve) => {
    let out = "";
    const child = spawn(cmd, args, { cwd: WORK, env: { ...process.env, ...NPM_ENV } });
    const collect = (d) => { out = (out + d.toString()).slice(-4000); };
    child.stdout.on("data", collect);
    child.stderr.on("data", collect);
    const timer = setTimeout(() => child.kill("SIGKILL"), timeoutMs);
    child.on("error", (e) => { clearTimeout(timer); resolve({ code: -1, out: e.message }); });
    child.on("exit", (code) => { clearTimeout(timer); resolve({ code: code === null ? -1 : code, out }); });
  });
}

const WORK = path.resolve(process.env.DEVOS_WORK || "/work");
const KEY_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;
const MAX_READ = 64 * 1024;

function safePath(rel) {
  if (typeof rel !== "string" || !rel) throw new Error("path is required");
  const full = path.resolve(WORK, rel);
  if (full !== WORK && !full.startsWith(WORK + path.sep)) throw new Error("path escapes project root");
  return full;
}

function parseEnv(text) {
  const out = {};
  for (const line of text.split(/\r?\n/)) {
    const m = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$/);
    if (m) out[m[1]] = m[2];
  }
  return out;
}

const handlers = {
  set_env_var({ path: rel = ".env", key, value }) {
    const file = safePath(rel);
    if (!path.basename(file).startsWith(".env")) throw new Error("only .env files may be edited");
    if (typeof key !== "string" || !KEY_RE.test(key)) throw new Error("invalid variable name");
    if (typeof value !== "string" || /[\r\n]/.test(value) || value.length > 1000) {
      throw new Error("invalid variable value");
    }
    const existing = fs.existsSync(file) ? fs.readFileSync(file, "utf8") : "";
    const lines = existing === "" ? [] : existing.replace(/\r?\n$/, "").split(/\r?\n/);
    const idx = lines.findIndex((l) => new RegExp(`^\\s*${key}\\s*=`).test(l));
    if (idx >= 0) lines[idx] = `${key}=${value}`;
    else lines.push(`${key}=${value}`);
    fs.writeFileSync(file, lines.join("\n") + "\n");
    return { ok: true, message: `set ${key} in ${path.relative(WORK, file)}` };
  },

  read_file({ path: rel }) {
    const content = fs.readFileSync(safePath(rel), "utf8");
    return { ok: true, content: content.slice(0, MAX_READ) };
  },

  /** Install what the project's own package.json lists. Takes NO package names and skips install
   * scripts, so it cannot pull in anything the project did not already declare. Needs network. */
  async install_dependency({ manager = "npm" }) {
    if (manager !== "npm") throw new Error(`package manager "${manager}" is not supported`);
    if (!fs.existsSync(path.join(WORK, "package.json"))) throw new Error("no package.json in the project");
    const { code, out } = await runFixed("npm", ["install", "--ignore-scripts", "--no-audit", "--no-fund"], 240000);
    if (code !== 0) throw new Error(`npm install failed (exit ${code}): ${out.trim().split("\n").slice(-3).join(" | ")}`);
    return { ok: true, message: "installed the packages listed in package.json (install scripts skipped)" };
  },

  /** Apply a .sql file to the project's own database (DATABASE_URL from its .env) in ONE
   * transaction, so a failing file changes nothing. Needs network to reach the database. */
  async run_migration({ path: rel }) {
    const file = safePath(rel);
    if (!file.endsWith(".sql")) throw new Error("only .sql files can be run as a migration");
    const sql = fs.readFileSync(file, "utf8");
    const envFile = path.join(WORK, ".env");
    const url = (fs.existsSync(envFile) ? parseEnv(fs.readFileSync(envFile, "utf8")) : {}).DATABASE_URL;
    if (!url) throw new Error("DATABASE_URL is not set in .env");

    const target = new URL(url);
    if (["localhost", "127.0.0.1", "[::1]"].includes(target.hostname)) target.hostname = "host.docker.internal";
    const secret = decodeURIComponent(target.password || "");
    const scrub = (m) => (secret ? String(m).split(secret).join("***") : String(m));

    const { Client } = require("pg");
    const client = new Client({ connectionString: target.toString(), connectionTimeoutMillis: 5000, statement_timeout: 30000 });
    try {
      await client.connect();
      await client.query("BEGIN");
      await client.query(sql);
      await client.query("COMMIT");
    } catch (e) {
      try { await client.query("ROLLBACK"); } catch { /* connection may already be gone */ }
      throw new Error(`migration failed and was rolled back: ${scrub(e.message)}`);
    } finally {
      await client.end().catch(() => {});
    }
    return { ok: true, message: `applied ${path.relative(WORK, file)} in one transaction` };
  },
};

async function runAction(action) {
  const handler = action && Object.prototype.hasOwnProperty.call(handlers, action.type) && handlers[action.type];
  if (!handler) return { ok: false, error: `action type "${action && action.type}" is not supported` };
  try {
    return await handler(action.params || {});
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

function checkHealth(port) {
  return new Promise((resolve) => {
    const req = http.get({ host: "127.0.0.1", port, path: "/health", timeout: 1000 }, (res) => {
      res.resume();
      resolve({ status: res.statusCode });
    });
    req.on("error", (e) => resolve({ status: null, error: e.code || e.message }));
    req.on("timeout", () => req.destroy(new Error("timeout")));
  });
}

/** Start the project's own `npm start` and see whether it stays up and answers /health. */
async function probe() {
  const pkgFile = path.join(WORK, "package.json");
  const pkg = fs.existsSync(pkgFile) ? JSON.parse(fs.readFileSync(pkgFile, "utf8")) : {};
  if (!pkg.scripts || !pkg.scripts.start) {
    return { startup: "not_run", reason: "project has no start script", health: null, log: "" };
  }
  const envFile = path.join(WORK, ".env");
  const fromFile = fs.existsSync(envFile) ? parseEnv(fs.readFileSync(envFile, "utf8")) : {};
  const port = Number(fromFile.PORT || process.env.PORT || 3000);

  let log = "";
  let exited = null;
  const child = spawn("npm", ["start", "--silent"], { cwd: WORK, env: { ...process.env, ...NPM_ENV } });
  const collect = (d) => { log = (log + d.toString()).slice(-4000); };
  child.stdout.on("data", collect);
  child.stderr.on("data", collect);
  child.on("exit", (code) => { exited = code === null ? -1 : code; });

  const deadline = Date.now() + 10000;
  let result = null;
  while (Date.now() < deadline && result === null) {
    await new Promise((r) => setTimeout(r, 250));
    if (exited !== null) {
      result = { startup: "crashed", exit_code: exited, health: null };
      break;
    }
    const health = await checkHealth(port);
    if (health.status !== null) result = { startup: "ok", health };
  }
  if (result === null) result = { startup: "timeout", health: null };

  if (exited === null) {
    child.kill("SIGTERM");
    await new Promise((r) => setTimeout(r, 500));
    if (exited === null) child.kill("SIGKILL");
  }
  return { ...result, port, log };
}

/** One of the project's own npm scripts ("build" or "test"), run to completion. */
async function runScript(pkg, name) {
  if (!pkg.scripts || !pkg.scripts[name]) return { status: "skipped" };
  const { code, out } = await runFixed("npm", ["run", name, "--silent"], 120000);
  return { status: code === 0 ? "passed" : "failed", exit_code: code, log: out.slice(-1200) };
}

/** Full verification: build, then start the app and probe it, then run the tests. */
async function verify() {
  const pkgFile = path.join(WORK, "package.json");
  const pkg = fs.existsSync(pkgFile) ? JSON.parse(fs.readFileSync(pkgFile, "utf8")) : {};
  const build = await runScript(pkg, "build");
  const started = await probe();
  const tests = await runScript(pkg, "test");
  return { ...started, build, tests };
}

async function main() {
  let payload;
  try {
    payload = JSON.parse(process.env.DEVOS_PAYLOAD || "");
  } catch {
    return { ok: false, error: "invalid DEVOS_PAYLOAD" };
  }
  if (payload.mode === "action") return runAction(payload.action);
  if (payload.mode === "probe") return { ok: true, ...(await probe()) };
  if (payload.mode === "verify") return { ok: true, ...(await verify()) };
  return { ok: false, error: `unknown mode "${payload.mode}"` };
}

main().then((result) => {
  console.log("DEVOS_RESULT:" + JSON.stringify(result));
  process.exit(result.ok === false ? 1 : 0);
});
