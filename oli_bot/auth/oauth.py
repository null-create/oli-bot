"""
OAuth 2.1 client-side authorization flow for MCP server authentication.

Supported OAuth functionalities:
- Protected Resource Metadata discovery (RFC 9728): retrieves resource metadata
  from the MCP server's /.well-known/oauth-protected-resource endpoint.
- Authorization Server Discovery: supports both OpenID Connect
  (/.well-known/openid-configuration) and OAuth Authorization Server Metadata
  (/.well-known/oauth-authorization-server) discovery endpoints, with automatic
  fallback between the two.
- Dynamic Client Registration (RFC 7591): automatically registers a client with
  the authorization server if a registration endpoint is advertised.
- Authorization Code flow with PKCE (RFC 7636, S256 challenge method): opens a
  browser for user consent and captures the authorization code via a temporary
  local HTTP callback server.
- Authorization Code exchange: trades the authorization code for an access token
  and optional refresh token at the token endpoint.
- Token refresh: exchanges a stored refresh token for a new access token using
  the refresh_token grant type.
- Bearer token injection: exposes the acquired access token as an
  Authorization: Bearer header for use in downstream MCP requests.
- Optional SSL/TLS support for the HTTP client, controlled via the SSL_ENABLED
  environment variable.

References:
- RFC 9728: https://datatracker.ietf.org/doc/html/rfc9728
- RFC 7591: https://datatracker.ietf.org/doc/html/rfc7591
- RFC 7636: https://datatracker.ietf.org/doc/html/rfc7636
"""

import os
import asyncio
import html
import httpx
import secrets
import hashlib
import base64
import logging
import webbrowser
import threading
from dataclasses import dataclass
from urllib.parse import urlencode, parse_qs, urlparse
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional, Dict

# Logger setup
logger = logging.getLogger(__name__)

# Configuration from environment variables
AUTH_SERVER_BASE_URL = os.getenv("AUTH_SERVER_BASE_URL", "https://localhost:8060")
SCOPES = tuple(
    scope
    for scope in os.getenv("SCOPES", "openid profile email User.Read").split()
    if scope
)

# SSL configuration - controlled via environment variable for flexibility in different environments (dev, staging, prod)
SSL_ENABLED = os.getenv("SSL_ENABLED", "false").lower() == "true"

CERT_FILE_PATH = (
    os.path.join(os.path.abspath(os.path.dirname(__file__)), "certs", "cert.pem")
    if SSL_ENABLED
    else None
)
if SSL_ENABLED and not os.path.exists(CERT_FILE_PATH):
    raise FileNotFoundError(
        f"SSL is enabled but certificate file not found at {CERT_FILE_PATH}. "
        "Please ensure the certificate file exists and the path is correct."
    )


@dataclass
class ClientRegistration:
    """Holds dynamic client registration data"""

    client_id: str
    client_secret: Optional[str]
    registration_access_token: Optional[str] = None


class CallbackHandler(BaseHTTPRequestHandler):
    """
    Temporary local HTTP server handler used during the OAuth 2.1 authorization
    code flow (Step 5 of OAuthHandler.discover_and_authorize).

    OAuthHandler spins up a short-lived HTTPServer on localhost (default port
    8040) and registers its redirect_uri with the authorization server.  After
    the user grants consent in the browser, the authorization server redirects
    back to that URI.  CallbackHandler intercepts that request, extracts the
    authorization code from the query string, and stores it as a class-level
    variable so the waiting OAuthHandler coroutine can pick it up and proceed
    to the token-exchange step (Step 6).

    Beyond the initial code-capture, the handler also serves a post-login page
    that exposes a "Log Out" button.  When clicked, it issues a GET /logout
    request back to this same server; the handler then calls the authorization
    server's revocation endpoint to invalidate the active access token before
    shutting the server down.

    Class-level variables are used (rather than instance variables) so that the
    state is accessible both from the synchronous BaseHTTPRequestHandler
    callbacks and from the async OAuthHandler coroutine running in a different
    thread.
    """

    authorization_code: Optional[str] = None
    iss: Optional[str] = None
    error: Optional[str] = None
    logout_requested: bool = False
    _access_token: Optional[str] = None
    _revocation_endpoint: Optional[str] = None
    _client_id: Optional[str] = None
    _client_secret: Optional[str] = None
    _server_ref: Optional["HTTPServer"] = None
    _auth_server_url: Optional[str] = None

    def do_GET(self):
        """Handle the OAuth callback"""
        query_components = parse_qs(urlparse(self.path).query)

        if self.path == "/logout":
            CallbackHandler.logout_requested = True
            # Delegate to the auth server's /auth/logout endpoint, which clears
            # server-side Azure tokens and redirects through Azure's end-session
            # endpoint — matching the behavior of the "Log Out" button on /test-auth.
            redirect_target = f"{AUTH_SERVER_BASE_URL}/test-auth"
            self.send_response(302)
            self.send_header("Location", redirect_target)
            self.end_headers()
            logger.info("[OAuth] Redirecting to auth server logout")
            if CallbackHandler._server_ref:
                threading.Thread(
                    target=CallbackHandler._server_ref.shutdown, daemon=True
                ).start()
        elif "code" in query_components:
            CallbackHandler.authorization_code = query_components["code"][0]
            # RFC 9207: record the issuer so the flow can validate it before redeeming the code
            CallbackHandler.iss = query_components.get("iss", [None])[0]
            auth_base_url = html.escape(
                CallbackHandler._auth_server_url or AUTH_SERVER_BASE_URL
            )
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(f"""
                <html>
                <body style="font-family: Arial, sans-serif; text-align: center; padding: 50px;">
                    <h1 style="color: #4CAF50;"> Authorization Successful!</h1>
                    <p>You can close this window and return to your application.</p>
                    <br>
                    <a href="{auth_base_url}/test-auth" style="display: inline-block; background-color: #f44336; color: white; padding: 10px 24px; border-radius: 4px; text-decoration: none; font-size: 14px;">Go to auth testing</a>
                </body>
                </html>
            """.encode("utf-8"))
        elif "error" in query_components:
            CallbackHandler.error = query_components["error"][0]
            error_text = html.escape(query_components["error"][0])
            self.send_response(400)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(f"""
                <html>
                <body style="font-family: Arial, sans-serif; text-align: center; padding: 50px;">
                    <h1 style="color: #f44336;"> Authorization Failed</h1>
                    <p>Error: {error_text}</p>
                </body>
                </html>
            """.encode())
        else:
            self.send_response(400)
            self.end_headers()

    def log_message(self, format, *args):
        """Suppress default logging"""
        pass


class OAuthHandler:
    """
    Orchestrates the full OAuth 2.1 authorization code + PKCE flow on behalf of
    the MCP client.

    Must be used as an async context manager to manage the underlying httpx
    session.

    Flow:

      Step 1-2  — GET /mcp → 401 WWW-Authenticate, then fetch Protected
                  Resource Metadata (RFC 9728) from /.well-known/oauth-
                  protected-resource on the auth proxy (:8060).

      Step 3    — Authorization Server Discovery: try OpenID Connect
                  /.well-known/openid-configuration, falling back to RFC 8414
                  /.well-known/oauth-authorization-server.

      Step 4    — Dynamic Client Registration (RFC 7591): POST /register to
                  the auth proxy to obtain a ephemeral client_id / secret.

      Step 5    — User Authorization: build a PKCE (S256) authorization URL,
                  open the browser, spin up CallbackHandler on localhost:8040
                  to capture the authorization code redirect.

      Step 6    — Token Exchange: POST /auth/token with the authorization code
                  + PKCE code_verifier; the auth proxy forwards to Azure Entra
                  ID and returns an access token (and optional refresh token).

      After     — The access token is attached as a Bearer header for all
                  subsequent GET /mcp requests to the MCP server (:8050).

    Token lifecycle methods:
      - discover_and_authorize()  — runs Steps 1-6 above, returns access_token
      - refresh_access_token()    — exchanges refresh_token for a new access_token
      - logout()                  — revokes the access token at the revocation endpoint
      - get_auth_headers()        — returns the Authorization: Bearer header dict
    """

    # Default callback port for the local OAuth callback server.
    # Must NOT conflict with the auth proxy server (port 8060).
    DEFAULT_CALLBACK_PORT = 8040

    def __init__(self, redirect_uri: str = None):
        self.callback_port = self.DEFAULT_CALLBACK_PORT
        self.redirect_uri = (
            redirect_uri or f"http://localhost:{self.callback_port}/callback"
        )
        self.http_client: Optional[httpx.AsyncClient] = None
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.token_endpoint: Optional[str] = None
        self.revocation_endpoint: Optional[str] = None
        self.issuer: Optional[str] = None
        self.iss_supported: bool = False
        self.client_registration: Optional[ClientRegistration] = None
        self.cert_file: Optional[str] = CERT_FILE_PATH if SSL_ENABLED else None

    async def __aenter__(self):
        """Async context manager entry"""
        if SSL_ENABLED and self.cert_file:
            logger.info("SSL is enabled for the auth server")
            self.http_client = httpx.AsyncClient(verify=self.cert_file, timeout=30)
        else:
            logger.debug(
                "SSL is disabled for the auth server. This is not recommended for "
                "production environments."
            )
            self.http_client = httpx.AsyncClient(timeout=30)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        if self.http_client:
            await self.http_client.aclose()

    @staticmethod
    def _generate_pkce_pair() -> tuple[str, str]:
        """Generate PKCE code verifier and challenge"""
        code_verifier = (
            base64.urlsafe_b64encode(secrets.token_bytes(32))
            .decode("utf-8")
            .rstrip("=")
        )
        code_challenge = (
            base64.urlsafe_b64encode(
                hashlib.sha256(code_verifier.encode("utf-8")).digest()
            )
            .decode("utf-8")
            .rstrip("=")
        )
        return code_verifier, code_challenge

    async def discover_and_authorize(self, mcp_server_url: str) -> str:
        """
        Complete OAuth 2.1 authorization flow
        Returns: access_token
        """
        logger.info("[OAuth] Step 1-2: Discovering authorization requirements...")

        # Step 1 & 2: Initial request and Protected Resource Metadata discovery
        try:
            response = await self.http_client.get(mcp_server_url)
            if response.status_code != 401:
                logger.warning("[OAuth] Server does not require authentication")
                return ""
        except Exception as e:
            raise Exception(f"Failed to connect to MCP server: {e}")

        # Parse WWW-Authenticate header
        www_auth = response.headers.get("WWW-Authenticate", "")
        if not www_auth:
            raise Exception("No WWW-Authenticate header found in 401 response")

        resource_metadata_url = None
        for part in www_auth.split(","):
            if "resource_metadata=" in part:
                resource_metadata_url = (
                    part.split("resource_metadata=")[1].strip('"').strip()
                )
                break

        if not resource_metadata_url:
            parsed_url = urlparse(mcp_server_url)
            resource_metadata_url = f"{parsed_url.scheme}://{parsed_url.netloc}/.well-known/oauth-protected-resource"

        # Retrieve Protected Resource Metadata
        try:
            prm_response = await self.http_client.get(resource_metadata_url)
            prm_response.raise_for_status()
            prm_data = prm_response.json()
            logger.info("[OAuth] Protected Resource Metadata retrieved")
        except Exception as e:
            raise Exception(f"Failed to retrieve Protected Resource Metadata: {e}")

        # Step 3: Authorization Server Discovery
        logger.info("[OAuth] Step 3: Discovering authorization server...")
        auth_server_url = prm_data["authorization_servers"][0]
        CallbackHandler._auth_server_url = auth_server_url
        discovery_url = (
            f"{auth_server_url.rstrip('/')}/.well-known/openid-configuration"
        )

        try:
            response = await self.http_client.get(discovery_url)
            response.raise_for_status()
            auth_metadata = response.json()
        except Exception as e:
            logger.warning(f"OpenID configuration discovery failed: {str(e)}")
            # Fallback to OAuth Authorization Server Metadata
            logger.warning("Trying OAuth Authorization Server Metadata...")
            oauth_discovery_url = (
                f"{auth_server_url.rstrip('/')}/.well-known/oauth-authorization-server"
            )
            response = await self.http_client.get(oauth_discovery_url)
            response.raise_for_status()
            auth_metadata = response.json()

        logger.info("[OAuth] Authorization server discovered")

        # Store the token endpoint for later use (token exchange & refresh)
        self.token_endpoint = auth_metadata["token_endpoint"]
        self.revocation_endpoint = auth_metadata.get("revocation_endpoint")
        # RFC 9207 (SEP-2468): record issuer for authorization-response iss validation
        self.issuer = auth_metadata.get("issuer", auth_server_url)
        self.iss_supported = auth_metadata.get(
            "authorization_response_iss_parameter_supported", False
        )

        # Step 4: Dynamic Client Registration
        # NOTE: this has been depreciated in favor of pre-registered credentials with the
        # latest MCP specification (7-28-2026), but is left here for backwards compatibility.
        logger.info(f"[OAuth] Step 4: Registering client...")
        if "registration_endpoint" in auth_metadata:
            registration_data = {
                "client_name": "MCP Client with OAuth",
                "redirect_uris": [self.redirect_uri],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "client_secret_post",
                "application_type": "web",
            }

            try:
                response = await self.http_client.post(
                    auth_metadata["registration_endpoint"],
                    json=registration_data,
                    headers={"Content-Type": "application/json"},
                )
                response.raise_for_status()
                registration_response = response.json()
            except Exception as e:
                raise Exception(f"Client registration failed: {e}")

            self.client_registration = ClientRegistration(
                client_id=registration_response["client_id"],
                client_secret=registration_response.get("client_secret"),
                registration_access_token=registration_response.get(
                    "registration_access_token"
                ),
            )
            logger.info(
                f"[OAuth] Client registered: {self.client_registration.client_id}"
            )
        else:
            raise Exception(
                "Dynamic Client Registration not supported. Please provide pre-registered credentials."
            )

        # Step 5: User Authorization
        logger.info("[OAuth] Step 5: Starting user authorization flow...")
        scopes = prm_data.get("scopes_supported", SCOPES)

        code_verifier, code_challenge = self._generate_pkce_pair()
        state = secrets.token_urlsafe(32)

        auth_params = {
            "client_id": self.client_registration.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }

        auth_url = f"{auth_metadata['authorization_endpoint']}?{urlencode(auth_params)}"

        # Start local callback server on the configured callback port
        CallbackHandler.authorization_code = None
        CallbackHandler.iss = None
        CallbackHandler.error = None
        CallbackHandler.logout_requested = False

        # Parse port from redirect_uri to handle custom ports
        callback_parsed = urlparse(self.redirect_uri)
        callback_port = callback_parsed.port or self.callback_port
        server = HTTPServer(("localhost", callback_port), CallbackHandler)
        CallbackHandler._server_ref = server
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        logger.info("[OAuth] Opening browser for authorization...")
        logger.info("[OAuth] URL: " + auth_url)
        webbrowser.open(auth_url)

        # Wait for callback
        timeout = 120
        try:
            for _ in range(timeout):
                await asyncio.sleep(1)
                if CallbackHandler.authorization_code or CallbackHandler.error:
                    break
        finally:
            # Shutdown server after authorization or timeout
            server.shutdown()
            server.server_close()
            thread.join()

        if CallbackHandler.error:
            raise Exception(f"Authorization failed: {CallbackHandler.error}")

        if not CallbackHandler.authorization_code:
            raise TimeoutError("Authorization timeout")

        # RFC 9207 (SEP-2468): validate the authorization-response issuer before redeeming the code
        returned_iss = CallbackHandler.iss
        if returned_iss is not None:
            if returned_iss != self.issuer:
                raise Exception(
                    f"Authorization response 'iss' mismatch: {returned_iss} != {self.issuer}"
                )
        elif self.iss_supported:
            raise Exception(
                "Authorization server advertises 'iss' support but none was returned"
            )

        logger.info("[OAuth] Authorization code received, exchanging for token...")

        # Step 6: Exchange authorization code for token via the auth server's
        # token endpoint, including the PKCE code_verifier for verification.
        token_request_data = {
            "grant_type": "authorization_code",
            "code": CallbackHandler.authorization_code,
            "redirect_uri": self.redirect_uri,
            "client_id": self.client_registration.client_id,
            "code_verifier": code_verifier,
        }
        if self.client_registration.client_secret:
            token_request_data["client_secret"] = self.client_registration.client_secret

        try:
            token_response = await self.http_client.post(
                self.token_endpoint,
                data=token_request_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            token_response.raise_for_status()
            token_data = token_response.json()
        except Exception as e:
            raise Exception(f"Token exchange failed: {e}")

        self.access_token = token_data.get("azure_access_token") or token_data.get(
            "access_token"
        )
        self.refresh_token = token_data.get("refresh_token")

        # Populate handler class vars so the logout button can revoke the token
        CallbackHandler._access_token = self.access_token
        CallbackHandler._revocation_endpoint = self.revocation_endpoint
        CallbackHandler._client_id = (
            self.client_registration.client_id if self.client_registration else None
        )
        CallbackHandler._client_secret = (
            self.client_registration.client_secret if self.client_registration else None
        )

        logger.info(f"[OAuth] ✓ Access token obtained successfully")
        return self.access_token

    async def refresh_access_token(self) -> str:
        """Refresh the access token using refresh token"""
        if not self.refresh_token:
            raise Exception("No refresh token available")

        logger.info("[OAuth] Refreshing access token...")

        token_data = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "client_id": self.client_registration.client_id,
        }

        if self.client_registration.client_secret:
            token_data["client_secret"] = self.client_registration.client_secret

        try:
            response = await self.http_client.post(
                self.token_endpoint,
                data=token_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
        except Exception as e:
            raise Exception(f"Failed to refresh access token: {e}")

        token_response = response.json()
        self.access_token = token_response["access_token"]
        if "refresh_token" in token_response:
            self.refresh_token = token_response["refresh_token"]

        logger.info("[OAuth] ✓ Access token refreshed")
        return self.access_token

    async def logout(self) -> None:
        """Revoke the access token and clear credentials"""
        if self.revocation_endpoint and self.access_token and self.client_registration:
            logger.info("[OAuth] Revoking access token...")
            revoke_data = {
                "token": self.access_token,
                "client_id": self.client_registration.client_id,
            }
            if self.client_registration.client_secret:
                revoke_data["client_secret"] = self.client_registration.client_secret
            try:
                response = await self.http_client.post(
                    self.revocation_endpoint,
                    data=revoke_data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                response.raise_for_status()
                logger.info("[OAuth] ✓ Access token revoked")
            except Exception as e:
                logger.warning(f"[OAuth] Token revocation failed: {e}")
        self.access_token = None
        self.refresh_token = None
        logger.info("[OAuth] Logged out")

    def get_auth_headers(self) -> Dict[str, str]:
        """Get authorization headers"""
        if not self.access_token:
            raise Exception("No access token available")
        return {"Authorization": f"Bearer {self.access_token}"}
