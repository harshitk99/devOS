# Sandbox

Docker image / profile used by `backend/app/services/sandbox.py` to run approved actions.

Every sandboxed run must apply the bounds in `backend/app/core/config.py` (`SandboxLimits`): CPU, memory, timeout, read-only root filesystem, and no network unless a specific approved action needs it.

To do: `Dockerfile` for the sandbox base image (Node toolchain for the demo app).
