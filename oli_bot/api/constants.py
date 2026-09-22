"""Shared constants for the API server package."""

# Sessions are namespaced per "server". The browser shares the TUI's store by
# using the same default namespace the TUI falls back to when no server is set.
SESSION_SERVER = "default"
