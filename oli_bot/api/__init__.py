"""FastAPI app package exposing the oli agent harness over an OpenAI-compatible
REST API plus a stateful WebSocket.

Importing this package has no side effects: the FastAPI app is built lazily via
``create_app()`` and agent/backend/MCP state is only constructed by ``init_state``
(called from the lifespan and from ``main()``).
"""
