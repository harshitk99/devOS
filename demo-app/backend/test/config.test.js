"use strict";
const test = require("node:test");
const assert = require("node:assert");
const { validateEnv } = require("../config");

test("a complete environment has no problems", () => {
  assert.deepStrictEqual(validateEnv({ PORT: "3100", DATABASE_URL: "postgresql://u:p@h/db" }), []);
});

test("missing variables are reported by name", () => {
  const errors = validateEnv({ PORT: "3100" });
  assert.strictEqual(errors.length, 1);
  assert.match(errors[0], /DATABASE_URL/);
});

test("a malformed database url and port are rejected", () => {
  const errors = validateEnv({ PORT: "abc", DATABASE_URL: "not-a-url" });
  assert.strictEqual(errors.length, 2);
});
