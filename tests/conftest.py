"""Shared test config: force offline mode by scrubbing OLI_* env vars."""

import os

# Never let a stray OLI_* env var pollute test defaults.
for key in list(os.environ):
    if key.startswith("OLI_"):
        os.environ.pop(key)
