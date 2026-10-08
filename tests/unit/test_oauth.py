import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from httpx._models import Headers

from oli_bot.auth.oauth import OAuthHandler


@pytest.mark.asyncio
async def test_discover_and_authorize_handles_missing_www_authenticate_header(
    mock_client,
):
    """401 response with no WWW-Authenticate header raises an exception."""
    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.headers = Headers({})

    mock_client.get.return_value = mock_response

    handler = OAuthHandler()
    handler.http_client = mock_client
    with pytest.raises(Exception) as context:
        await handler.discover_and_authorize("https://example.com")

    assert "No WWW-Authenticate header found" in str(context.value)


@pytest.mark.asyncio
async def test_discover_and_authorize_handles_authorization_timeout(mock_client):
    """Authorization code is never received within the timeout window; raises TimeoutError."""
    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.headers = Headers(
        {
            "WWW-Authenticate": 'resource_metadata="https://example.com/.well-known/oauth-protected-resource"'
        }
    )

    mock_prm_response = MagicMock()
    mock_prm_response.json.return_value = {
        "authorization_servers": ["https://auth.example.com"],
        "scopes_supported": ["scope1", "scope2"],
    }

    mock_auth_metadata_response = MagicMock()
    mock_auth_metadata_response.json.return_value = {
        "authorization_endpoint": "https://auth.example.com/authorize",
        "token_endpoint": "https://auth.example.com/auth/token",
        "registration_endpoint": "https://auth.example.com/register",
    }

    mock_registration_response = MagicMock()
    mock_registration_response.json.return_value = {
        "client_id": "test_client_id",
        "client_secret": "test_client_secret",
    }

    mock_client.get.side_effect = [
        mock_response,
        mock_prm_response,
        mock_auth_metadata_response,
    ]
    mock_client.post.return_value = mock_registration_response

    with (
        patch("oli_bot.auth.oauth.webbrowser.open"),
        patch("oli_bot.auth.oauth.CallbackHandler.authorization_code", None),
        patch("oli_bot.auth.oauth.asyncio.sleep", new_callable=AsyncMock),
        patch("oli_bot.auth.oauth.HTTPServer"),
    ):
        handler = OAuthHandler()
        handler.http_client = mock_client
        with pytest.raises(TimeoutError) as context:
            await handler.discover_and_authorize("https://example.com")

    assert "Authorization timeout" in str(context.value)


@pytest.mark.asyncio
async def test_discover_and_authorize_returns_access_token(mock_client):
    """Full happy-path flow completes and returns the access token."""
    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.headers = Headers(
        {
            "WWW-Authenticate": 'resource_metadata="https://example.com/.well-known/oauth-protected-resource"'
        }
    )

    mock_prm_response = MagicMock()
    mock_prm_response.json.return_value = {
        "authorization_servers": ["https://auth.example.com"],
        "scopes_supported": ["scope1", "scope2"],
    }

    mock_auth_metadata_response = MagicMock()
    mock_auth_metadata_response.json.return_value = {
        "authorization_endpoint": "https://auth.example.com/authorize",
        "token_endpoint": "https://auth.example.com/auth/token",
        "registration_endpoint": "https://auth.example.com/register",
    }

    mock_registration_response = MagicMock()
    mock_registration_response.json.return_value = {
        "client_id": "test_client_id",
        "client_secret": "test_client_secret",
    }

    mock_token_response = MagicMock()
    mock_token_response.status_code = 200
    mock_token_response.json.return_value = {
        "access_token": "test_access_token",
        "azure_access_token": "test_access_token",
        "token_type": "Bearer",
        "refresh_token": "test_refresh_token",
    }
    mock_token_response.raise_for_status = MagicMock()

    # Set side_effect ONCE with all responses in order
    mock_client.get.side_effect = [
        mock_response,
        mock_prm_response,
        mock_auth_metadata_response,
    ]
    # post is called twice: first for registration, then for token exchange
    mock_client.post.side_effect = [
        mock_registration_response,
        mock_token_response,
    ]

    async def fake_sleep(_):
        from oli_bot.auth.oauth import CallbackHandler as CH

        CH.authorization_code = "test_code"

    with (
        patch("oli_bot.auth.oauth.webbrowser.open"),
        patch("oli_bot.auth.oauth.asyncio.sleep", side_effect=fake_sleep),
        patch("oli_bot.auth.oauth.HTTPServer"),
    ):
        handler = OAuthHandler()
        handler.http_client = mock_client
        access_token = await handler.discover_and_authorize("https://example.com")

    assert access_token == "test_access_token"


@pytest.mark.asyncio
async def test_discover_and_authorize_rejects_mismatched_iss(mock_client):
    """RFC 9207: a returned iss that differs from the recorded issuer aborts the flow."""
    mock_401 = MagicMock()
    mock_401.status_code = 401
    mock_401.headers = Headers(
        {
            "WWW-Authenticate": 'resource_metadata="https://example.com/.well-known/oauth-protected-resource"'
        }
    )

    mock_prm = MagicMock()
    mock_prm.raise_for_status = MagicMock()
    mock_prm.json.return_value = {
        "authorization_servers": ["https://auth.example.com"],
        "scopes_supported": ["openid"],
    }

    mock_auth_meta = MagicMock()
    mock_auth_meta.raise_for_status = MagicMock()
    mock_auth_meta.json.return_value = {
        "issuer": "https://auth.example.com",
        "authorization_endpoint": "https://auth.example.com/authorize",
        "token_endpoint": "https://auth.example.com/auth/token",
        "registration_endpoint": "https://auth.example.com/register",
    }

    mock_reg = MagicMock()
    mock_reg.raise_for_status = MagicMock()
    mock_reg.json.return_value = {"client_id": "cid", "client_secret": "csecret"}

    mock_client.get.side_effect = [mock_401, mock_prm, mock_auth_meta]
    mock_client.post.return_value = mock_reg

    async def fake_sleep(_):
        from oli_bot.auth.oauth import CallbackHandler as CH

        CH.authorization_code = "test_code"
        CH.iss = "https://evil.example.com"

    with (
        patch("oli_bot.auth.oauth.webbrowser.open"),
        patch("oli_bot.auth.oauth.asyncio.sleep", side_effect=fake_sleep),
        patch("oli_bot.auth.oauth.HTTPServer"),
    ):
        handler = OAuthHandler()
        handler.http_client = mock_client
        with pytest.raises(Exception) as context:
            await handler.discover_and_authorize("https://example.com")

    assert "'iss' mismatch" in str(context.value)


@pytest.mark.asyncio
async def test_discover_and_authorize_rejects_missing_iss_when_supported(mock_client):
    """RFC 9207: a missing iss is rejected when the AS advertises iss support."""
    mock_401 = MagicMock()
    mock_401.status_code = 401
    mock_401.headers = Headers(
        {
            "WWW-Authenticate": 'resource_metadata="https://example.com/.well-known/oauth-protected-resource"'
        }
    )

    mock_prm = MagicMock()
    mock_prm.raise_for_status = MagicMock()
    mock_prm.json.return_value = {
        "authorization_servers": ["https://auth.example.com"],
        "scopes_supported": ["openid"],
    }

    mock_auth_meta = MagicMock()
    mock_auth_meta.raise_for_status = MagicMock()
    mock_auth_meta.json.return_value = {
        "issuer": "https://auth.example.com",
        "authorization_endpoint": "https://auth.example.com/authorize",
        "token_endpoint": "https://auth.example.com/auth/token",
        "registration_endpoint": "https://auth.example.com/register",
        "authorization_response_iss_parameter_supported": True,
    }

    mock_reg = MagicMock()
    mock_reg.raise_for_status = MagicMock()
    mock_reg.json.return_value = {"client_id": "cid", "client_secret": "csecret"}

    mock_client.get.side_effect = [mock_401, mock_prm, mock_auth_meta]
    mock_client.post.return_value = mock_reg

    async def fake_sleep(_):
        from oli_bot.auth.oauth import CallbackHandler as CH

        CH.authorization_code = "test_code"  # iss deliberately left unset (None)

    with (
        patch("oli_bot.auth.oauth.webbrowser.open"),
        patch("oli_bot.auth.oauth.asyncio.sleep", side_effect=fake_sleep),
        patch("oli_bot.auth.oauth.HTTPServer"),
    ):
        handler = OAuthHandler()
        handler.http_client = mock_client
        with pytest.raises(Exception) as context:
            await handler.discover_and_authorize("https://example.com")

    assert "advertises 'iss' support" in str(context.value)


# ──────────────────────────────────────────────────────────────
# discover_and_authorize — edge cases
# ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_discover_and_authorize_returns_empty_when_no_auth_required(mock_client):
    """Server responds 200 — no authentication needed."""
    mock_response = MagicMock()
    mock_response.status_code = 200

    mock_client.get.return_value = mock_response

    handler = OAuthHandler()
    handler.http_client = mock_client
    result = await handler.discover_and_authorize("https://example.com")

    assert result == ""


@pytest.mark.asyncio
async def test_discover_and_authorize_raises_on_connection_failure(mock_client):
    """Network error when reaching the MCP server."""
    mock_client.get.side_effect = Exception("Connection refused")

    handler = OAuthHandler()
    handler.http_client = mock_client
    with pytest.raises(Exception) as context:
        await handler.discover_and_authorize("https://example.com")

    assert "Failed to connect to MCP server" in str(context.value)


@pytest.mark.asyncio
async def test_discover_and_authorize_constructs_fallback_prm_url(mock_client):
    """WWW-Authenticate has no resource_metadata= — falls back to /.well-known path."""
    mock_401 = MagicMock()
    mock_401.status_code = 401
    mock_401.headers = Headers({"WWW-Authenticate": "Bearer"})

    # The fallback PRM URL will be https://example.com/.well-known/oauth-protected-resource
    mock_prm = MagicMock()
    mock_prm.raise_for_status = MagicMock()
    mock_prm.json.return_value = {
        "authorization_servers": ["https://auth.example.com"],
        "scopes_supported": ["openid"],
    }

    mock_auth_meta = MagicMock()
    mock_auth_meta.raise_for_status = MagicMock()
    mock_auth_meta.json.return_value = {
        "authorization_endpoint": "https://auth.example.com/authorize",
        "token_endpoint": "https://auth.example.com/auth/token",
        "registration_endpoint": "https://auth.example.com/register",
    }

    mock_reg = MagicMock()
    mock_reg.raise_for_status = MagicMock()
    mock_reg.json.return_value = {
        "client_id": "cid",
        "client_secret": "csecret",
    }

    mock_client.get.side_effect = [mock_401, mock_prm, mock_auth_meta]
    mock_client.post.return_value = mock_reg

    with (
        patch("oli_bot.auth.oauth.webbrowser.open"),
        patch("oli_bot.auth.oauth.CallbackHandler.authorization_code", None),
        patch("oli_bot.auth.oauth.asyncio.sleep", new_callable=AsyncMock),
        patch("oli_bot.auth.oauth.HTTPServer"),
    ):
        handler = OAuthHandler()
        handler.http_client = mock_client
        with pytest.raises(TimeoutError):
            await handler.discover_and_authorize("https://example.com")

    # Verify second GET was to the fallback URL
    second_get_url = mock_client.get.call_args_list[1][0][0]
    assert second_get_url == "https://example.com/.well-known/oauth-protected-resource"


@pytest.mark.asyncio
async def test_discover_and_authorize_raises_on_prm_fetch_failure(mock_client):
    """PRM fetch fails — propagates as a descriptive error."""
    mock_401 = MagicMock()
    mock_401.status_code = 401
    mock_401.headers = Headers(
        {
            "WWW-Authenticate": 'resource_metadata="https://example.com/.well-known/oauth-protected-resource"'
        }
    )

    mock_prm = MagicMock()
    mock_prm.raise_for_status.side_effect = Exception("500 Server Error")

    mock_client.get.side_effect = [mock_401, mock_prm]

    handler = OAuthHandler()
    handler.http_client = mock_client
    with pytest.raises(Exception) as context:
        await handler.discover_and_authorize("https://example.com")

    assert "Failed to retrieve Protected Resource Metadata" in str(context.value)


@pytest.mark.asyncio
async def test_discover_and_authorize_falls_back_to_oauth_discovery(mock_client):
    """OpenID discovery fails; handler retries with oauth-authorization-server URL."""
    mock_401 = MagicMock()
    mock_401.status_code = 401
    mock_401.headers = Headers(
        {
            "WWW-Authenticate": 'resource_metadata="https://example.com/.well-known/oauth-protected-resource"'
        }
    )

    mock_prm = MagicMock()
    mock_prm.raise_for_status = MagicMock()
    mock_prm.json.return_value = {
        "authorization_servers": ["https://auth.example.com"],
        "scopes_supported": ["openid"],
    }

    # OpenID config — raise_for_status throws
    mock_openid_failure = MagicMock()
    mock_openid_failure.raise_for_status.side_effect = Exception("404 Not Found")

    # OAuth AS metadata — succeeds
    mock_oauth_meta = MagicMock()
    mock_oauth_meta.raise_for_status = MagicMock()
    mock_oauth_meta.json.return_value = {
        "authorization_endpoint": "https://auth.example.com/authorize",
        "token_endpoint": "https://auth.example.com/auth/token",
        "registration_endpoint": "https://auth.example.com/register",
    }

    mock_reg = MagicMock()
    mock_reg.raise_for_status = MagicMock()
    mock_reg.json.return_value = {"client_id": "cid", "client_secret": "csecret"}

    mock_client.get.side_effect = [
        mock_401,
        mock_prm,
        mock_openid_failure,
        mock_oauth_meta,
    ]
    mock_client.post.return_value = mock_reg

    with (
        patch("oli_bot.auth.oauth.webbrowser.open"),
        patch("oli_bot.auth.oauth.CallbackHandler.authorization_code", None),
        patch("oli_bot.auth.oauth.asyncio.sleep", new_callable=AsyncMock),
        patch("oli_bot.auth.oauth.HTTPServer"),
    ):
        handler = OAuthHandler()
        handler.http_client = mock_client
        with pytest.raises(TimeoutError):
            await handler.discover_and_authorize("https://example.com")

    # Fourth GET should be the oauth-authorization-server fallback
    fourth_get_url = mock_client.get.call_args_list[3][0][0]
    assert "oauth-authorization-server" in fourth_get_url


@pytest.mark.asyncio
async def test_discover_and_authorize_raises_when_no_registration_endpoint(mock_client):
    """Auth metadata has no registration_endpoint — raises a clear error."""
    mock_401 = MagicMock()
    mock_401.status_code = 401
    mock_401.headers = Headers(
        {
            "WWW-Authenticate": 'resource_metadata="https://example.com/.well-known/oauth-protected-resource"'
        }
    )

    mock_prm = MagicMock()
    mock_prm.raise_for_status = MagicMock()
    mock_prm.json.return_value = {
        "authorization_servers": ["https://auth.example.com"],
        "scopes_supported": ["openid"],
    }

    mock_auth_meta = MagicMock()
    mock_auth_meta.raise_for_status = MagicMock()
    mock_auth_meta.json.return_value = {
        "authorization_endpoint": "https://auth.example.com/authorize",
        "token_endpoint": "https://auth.example.com/auth/token",
        # registration_endpoint deliberately absent
    }

    mock_client.get.side_effect = [mock_401, mock_prm, mock_auth_meta]

    handler = OAuthHandler()
    handler.http_client = mock_client
    with pytest.raises(Exception) as context:
        await handler.discover_and_authorize("https://example.com")

    assert "Dynamic Client Registration not supported" in str(context.value)


@pytest.mark.asyncio
async def test_discover_and_authorize_raises_on_registration_failure(mock_client):
    """Client registration POST fails — raises a descriptive error."""
    mock_401 = MagicMock()
    mock_401.status_code = 401
    mock_401.headers = Headers(
        {
            "WWW-Authenticate": 'resource_metadata="https://example.com/.well-known/oauth-protected-resource"'
        }
    )

    mock_prm = MagicMock()
    mock_prm.raise_for_status = MagicMock()
    mock_prm.json.return_value = {
        "authorization_servers": ["https://auth.example.com"],
        "scopes_supported": ["openid"],
    }

    mock_auth_meta = MagicMock()
    mock_auth_meta.raise_for_status = MagicMock()
    mock_auth_meta.json.return_value = {
        "authorization_endpoint": "https://auth.example.com/authorize",
        "token_endpoint": "https://auth.example.com/auth/token",
        "registration_endpoint": "https://auth.example.com/register",
    }

    mock_reg = MagicMock()
    mock_reg.raise_for_status.side_effect = Exception("403 Forbidden")

    mock_client.get.side_effect = [mock_401, mock_prm, mock_auth_meta]
    mock_client.post.return_value = mock_reg

    handler = OAuthHandler()
    handler.http_client = mock_client
    with pytest.raises(Exception) as context:
        await handler.discover_and_authorize("https://example.com")

    assert "Client registration failed" in str(context.value)


@pytest.mark.asyncio
async def test_discover_and_authorize_raises_on_callback_error(mock_client):
    """Authorization server redirects with an error parameter."""
    mock_401 = MagicMock()
    mock_401.status_code = 401
    mock_401.headers = Headers(
        {
            "WWW-Authenticate": 'resource_metadata="https://example.com/.well-known/oauth-protected-resource"'
        }
    )

    mock_prm = MagicMock()
    mock_prm.raise_for_status = MagicMock()
    mock_prm.json.return_value = {
        "authorization_servers": ["https://auth.example.com"],
        "scopes_supported": ["openid"],
    }

    mock_auth_meta = MagicMock()
    mock_auth_meta.raise_for_status = MagicMock()
    mock_auth_meta.json.return_value = {
        "authorization_endpoint": "https://auth.example.com/authorize",
        "token_endpoint": "https://auth.example.com/auth/token",
        "registration_endpoint": "https://auth.example.com/register",
    }

    mock_reg = MagicMock()
    mock_reg.raise_for_status = MagicMock()
    mock_reg.json.return_value = {"client_id": "cid", "client_secret": "csecret"}

    mock_client.get.side_effect = [mock_401, mock_prm, mock_auth_meta]
    mock_client.post.return_value = mock_reg

    async def fake_sleep_with_error(_):
        from oli_bot.auth.oauth import CallbackHandler as CH

        CH.error = "access_denied"

    with (
        patch("oli_bot.auth.oauth.webbrowser.open"),
        patch("oli_bot.auth.oauth.asyncio.sleep", side_effect=fake_sleep_with_error),
        patch("oli_bot.auth.oauth.HTTPServer"),
    ):
        handler = OAuthHandler()
        handler.http_client = mock_client
        with pytest.raises(Exception) as context:
            await handler.discover_and_authorize("https://example.com")

    assert "Authorization failed" in str(context.value)
    assert "access_denied" in str(context.value)


@pytest.mark.asyncio
async def test_discover_and_authorize_raises_on_token_exchange_failure(mock_client):
    """Token exchange POST fails — raises a descriptive error."""
    mock_401 = MagicMock()
    mock_401.status_code = 401
    mock_401.headers = Headers(
        {
            "WWW-Authenticate": 'resource_metadata="https://example.com/.well-known/oauth-protected-resource"'
        }
    )

    mock_prm = MagicMock()
    mock_prm.raise_for_status = MagicMock()
    mock_prm.json.return_value = {
        "authorization_servers": ["https://auth.example.com"],
        "scopes_supported": ["openid"],
    }

    mock_auth_meta = MagicMock()
    mock_auth_meta.raise_for_status = MagicMock()
    mock_auth_meta.json.return_value = {
        "authorization_endpoint": "https://auth.example.com/authorize",
        "token_endpoint": "https://auth.example.com/auth/token",
        "registration_endpoint": "https://auth.example.com/register",
    }

    mock_reg = MagicMock()
    mock_reg.raise_for_status = MagicMock()
    mock_reg.json.return_value = {"client_id": "cid", "client_secret": "csecret"}

    mock_token = MagicMock()
    mock_token.raise_for_status.side_effect = Exception("401 Unauthorized")

    mock_client.get.side_effect = [mock_401, mock_prm, mock_auth_meta]
    mock_client.post.side_effect = [mock_reg, mock_token]

    async def fake_sleep(_):
        from oli_bot.auth.oauth import CallbackHandler as CH

        CH.authorization_code = "test_code"

    with (
        patch("oli_bot.auth.oauth.webbrowser.open"),
        patch("oli_bot.auth.oauth.asyncio.sleep", side_effect=fake_sleep),
        patch("oli_bot.auth.oauth.HTTPServer"),
    ):
        handler = OAuthHandler()
        handler.http_client = mock_client
        with pytest.raises(Exception) as context:
            await handler.discover_and_authorize("https://example.com")

    assert "Token exchange failed" in str(context.value)


# ──────────────────────────────────────────────────────────────
# refresh_access_token tests
# ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_access_token_raises_when_no_refresh_token(mock_client):
    """No refresh token stored; raises an exception."""
    handler = OAuthHandler()
    handler.http_client = mock_client
    handler.refresh_token = None

    with pytest.raises(Exception) as context:
        await handler.refresh_access_token()

    assert "No refresh token available" in str(context.value)


@pytest.mark.asyncio
async def test_refresh_access_token_success(mock_client):
    """Successful refresh: updates access_token and refresh_token on the
    handler and returns the new access token."""
    from oli_bot.auth.oauth import ClientRegistration

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "access_token": "new_access_token",
        "refresh_token": "new_refresh_token",
    }
    mock_client.post.return_value = mock_response

    handler = OAuthHandler()
    handler.http_client = mock_client
    handler.refresh_token = "old_refresh_token"
    handler.token_endpoint = "https://auth.example.com/auth/token"
    handler.client_registration = ClientRegistration(
        client_id="cid", client_secret="csecret"
    )

    result = await handler.refresh_access_token()

    assert result == "new_access_token"
    assert handler.access_token == "new_access_token"
    assert handler.refresh_token == "new_refresh_token"


@pytest.mark.asyncio
async def test_refresh_access_token_retains_old_refresh_token_when_not_rotated(
    mock_client,
):
    """When the server omits refresh_token in the response, the old one is kept."""
    from oli_bot.auth.oauth import ClientRegistration

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"access_token": "new_access_token"}
    mock_client.post.return_value = mock_response

    handler = OAuthHandler()
    handler.http_client = mock_client
    handler.refresh_token = "old_refresh_token"
    handler.token_endpoint = "https://auth.example.com/auth/token"
    handler.client_registration = ClientRegistration(
        client_id="cid", client_secret="csecret"
    )

    await handler.refresh_access_token()

    assert handler.refresh_token == "old_refresh_token"


@pytest.mark.asyncio
async def test_refresh_access_token_raises_on_http_failure(mock_client):
    """Refresh POST returns an HTTP error; raises a descriptive exception."""
    from oli_bot.auth.oauth import ClientRegistration

    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = Exception("401 Unauthorized")
    mock_client.post.return_value = mock_response

    handler = OAuthHandler()
    handler.http_client = mock_client
    handler.refresh_token = "some_refresh_token"
    handler.token_endpoint = "https://auth.example.com/auth/token"
    handler.client_registration = ClientRegistration(
        client_id="cid", client_secret="csecret"
    )

    with pytest.raises(Exception) as context:
        await handler.refresh_access_token()

    assert "Failed to refresh access token" in str(context.value)


# ──────────────────────────────────────────────────────────────
# get_auth_headers tests
# ──────────────────────────────────────────────────────────────


def test_get_auth_headers_returns_bearer_header():
    """Returns {'Authorization': 'Bearer <token>'} when an access token exists."""
    handler = OAuthHandler()
    handler.access_token = "my_token"

    headers = handler.get_auth_headers()

    assert headers == {"Authorization": "Bearer my_token"}


def test_get_auth_headers_raises_when_no_access_token():
    """No access token stored; raises an exception."""
    handler = OAuthHandler()

    with pytest.raises(Exception) as context:
        handler.get_auth_headers()

    assert "No access token available" in str(context.value)
