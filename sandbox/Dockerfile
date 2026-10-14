# DevOS sandbox image. Runs ONLY the fixed handlers in runner.js against a project mounted at
# /work. Resource limits, the network mode (none, except for the two actions that need it), the
# read-only root and dropped capabilities are applied by backend/app/services/sandbox.py at
# `docker run` time (see core/config.SandboxLimits).
FROM node:20-alpine

# `pg` lets the run_migration handler talk to Postgres without trusting the project's own node_modules.
RUN npm install --prefix /opt/devos --no-audit --no-fund --ignore-scripts pg@8
ENV NODE_PATH=/opt/devos/node_modules

COPY runner.js /opt/devos/runner.js

USER node
WORKDIR /work
ENTRYPOINT ["node", "/opt/devos/runner.js"]
