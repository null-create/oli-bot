"""Shared test config: force offline mode by scrubbing OLI_* env vars.

Scrubs process env only — the repo-root `.env` file is not read by tests
except where AppConfig is built without `_env_file=None`; code paths that
must stay hermetic pass `_env_file=None` explicitly.
"""

import os

# Never let a stray OLI_* env var pollute test defaults.
for key in list(os.environ):
    if key.startswith("OLI_"):
        os.environ.pop(key)
