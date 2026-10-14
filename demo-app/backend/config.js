"use strict";
// Startup configuration checks, kept free of side effects so they can be unit tested.
const REQUIRED = ["PORT", "DATABASE_URL"];

function validateEnv(env) {
  const errors = [];
  for (const key of REQUIRED) {
    if (!env[key]) errors.push(`Missing required environment variable: ${key}`);
  }
  if (env.PORT && !/^\d+$/.test(env.PORT)) errors.push(`PORT must be a number, got "${env.PORT}"`);
  if (env.DATABASE_URL && !/^postgres(ql)?:\/\//.test(env.DATABASE_URL)) {
    errors.push("DATABASE_URL must start with postgresql://");
  }
  return errors;
}

module.exports = { REQUIRED, validateEnv };
