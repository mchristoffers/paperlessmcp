"""Tiny OAuth 2.1 authorization server for the gateway.

Deliberately dependency-free (stdlib only) and deliberately small: one static
client, one user, authorization-code + PKCE, HS256 JWTs. It exists to put a
login in front of upstreams that have none, not to be a general-purpose IdP.

Endpoints:
  POST /register                             fake DCR — always the static client
  GET  /.well-known/oauth-authorization-server
  GET  /.well-known/openid-configuration     (same document)
  GET  /authorize                            login form
  POST /authorize                            check credentials -> issue code
  POST /token                                code -> access/refresh token
  GET  /verify                               nginx auth_request target
"""

import base64
import hashlib
import hmac
import html
import json
import os
import secrets
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CONFIG_PATH = os.environ.get("SERVICES_JSON", "/app/services.json")
with open(CONFIG_PATH) as fh:
    CONFIG = json.load(fh)

ISSUER = os.environ.get("ISSUER") or CONFIG["issuer"]
CLIENT_ID = CONFIG["client"]["client_id"]
REDIRECT_URIS = CONFIG["client"]["redirect_uris"]

# What an MCP client is told it is authenticating *to*. Defaults to the
# issuer; set "protected_resource" in services.json to point at the exact
# endpoint (e.g. https://host/mcp) when a client is picky about the match.
PROTECTED_RESOURCE = os.environ.get("PROTECTED_RESOURCE") or CONFIG.get("protected_resource") or ISSUER

CLIENT_SECRET = os.environ["OAUTH_CLIENT_SECRET"]
USERNAME = os.environ["GATEWAY_USERNAME"]
PASSWORD = os.environ["GATEWAY_PASSWORD"]
JWT_SECRET = os.environ["JWT_SECRET"]

ACCESS_TTL = int(os.environ.get("ACCESS_TOKEN_TTL", "3600"))
REFRESH_TTL = int(os.environ.get("REFRESH_TOKEN_TTL", str(30 * 24 * 3600)))

# Authorization codes are single-use and short-lived, so in-memory is fine;
# a restart mid-login just means the user logs in again. Tokens are stateless
# JWTs and DO survive a restart, since JWT_SECRET comes from .env.
CODES = {}
CODE_TTL = 300


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def b64url_decode(txt: str) -> bytes:
    return base64.urlsafe_b64decode(txt + "=" * (-len(txt) % 4))


def jwt_encode(claims: dict) -> str:
    header = b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = b64url(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}".encode()
    sig = hmac.new(JWT_SECRET.encode(), signing_input, hashlib.sha256).digest()
    return f"{header}.{payload}.{b64url(sig)}"


def jwt_decode(token: str) -> dict | None:
    try:
        header_b64, payload_b64, sig_b64 = token.split(".")
    except ValueError:
        return None
    signing_input = f"{header_b64}.{payload_b64}".encode()
    expected = hmac.new(JWT_SECRET.encode(), signing_input, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, b64url_decode(sig_b64)):
        return None
    try:
        claims = json.loads(b64url_decode(payload_b64))
    except (ValueError, json.JSONDecodeError):
        return None
    if claims.get("exp", 0) < time.time():
        return None
    if claims.get("iss") != ISSUER:
        return None
    return claims


def purge_codes() -> None:
    now = time.time()
    for code, entry in list(CODES.items()):
        if entry["expires"] < now:
            del CODES[code]


LOGIN_PAGE = """<!doctype html>
<title>Sign in</title>
<style>
 body{{font-family:system-ui,sans-serif;background:#f6f7f9;display:flex;
      min-height:100vh;align-items:center;justify-content:center;margin:0}}
 form{{background:#fff;padding:2rem;border-radius:12px;min-width:300px;
      box-shadow:0 1px 3px rgba(0,0,0,.12)}}
 h1{{font-size:1.1rem;margin:0 0 1.25rem}}
 label{{display:block;font-size:.8rem;color:#555;margin:.75rem 0 .25rem}}
 input{{width:100%;padding:.55rem;border:1px solid #ccd;border-radius:6px;
       box-sizing:border-box;font-size:1rem}}
 button{{width:100%;margin-top:1.25rem;padding:.6rem;border:0;border-radius:6px;
        background:#2f6feb;color:#fff;font-size:1rem;cursor:pointer}}
 .err{{color:#b3261e;font-size:.85rem;margin-top:.75rem}}
</style>
<form method="post">
  <h1>Sign in to continue</h1>
  {fields}
  <label>Username</label>
  <input name="username" autofocus autocomplete="username">
  <label>Password</label>
  <input name="password" type="password" autocomplete="current-password">
  <button type="submit">Sign in</button>
  {error}
</form>
"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "oauth-agents"

    def log_message(self, fmt, *args):  # keep the log line short and quiet
        print(f"{self.address_string()} {fmt % args}", flush=True)

    # ---------- helpers ----------

    def send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, status: int, body: str) -> None:
        raw = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def send_empty(self, status: int, headers: dict | None = None) -> None:
        self.send_response(status)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def read_form(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode() if length else ""
        return {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            return {}

    # ---------- routes ----------

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/.well-known/oauth-authorization-server", "/.well-known/openid-configuration"):
            return self.metadata()
        # MCP clients look here first to find out which authorization server
        # guards this resource. Path suffixes are allowed by the spec, so match
        # the prefix rather than the exact string.
        if path.startswith("/.well-known/oauth-protected-resource"):
            return self.send_json(200, {
                "resource": PROTECTED_RESOURCE,
                "authorization_servers": [ISSUER],
            })
        if path == "/authorize":
            return self.authorize_form()
        if path == "/verify":
            return self.verify()
        if path == "/healthz":
            return self.send_json(200, {"ok": True})
        return self.send_json(404, {"error": "not_found"})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/register":
            return self.register()
        if path == "/authorize":
            return self.authorize_submit()
        if path == "/token":
            return self.token()
        return self.send_json(404, {"error": "not_found"})

    def metadata(self):
        self.send_json(200, {
            "issuer": ISSUER,
            "authorization_endpoint": f"{ISSUER}/authorize",
            "token_endpoint": f"{ISSUER}/token",
            "registration_endpoint": f"{ISSUER}/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": [
                "client_secret_post", "client_secret_basic", "none",
            ],
            "scopes_supported": ["openid", "offline_access"],
        })

    def register(self):
        """Fake DCR: hand back the one static client, never mint a new one."""
        self.read_json()  # drain the body; the client's metadata is ignored
        self.send_json(201, {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "client_id_issued_at": 0,
            "client_secret_expires_at": 0,
            "redirect_uris": REDIRECT_URIS,
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "client_secret_post",
        })

    def _authorize_params(self, query: dict) -> tuple[dict | None, str | None]:
        redirect_uri = query.get("redirect_uri", [""])[0]
        if redirect_uri not in REDIRECT_URIS:
            return None, "redirect_uri not registered"
        if query.get("client_id", [""])[0] != CLIENT_ID:
            return None, "unknown client_id"
        if query.get("code_challenge_method", ["S256"])[0] != "S256":
            return None, "code_challenge_method must be S256"
        if not query.get("code_challenge", [""])[0]:
            return None, "code_challenge required (PKCE is mandatory)"
        return {
            "redirect_uri": redirect_uri,
            "state": query.get("state", [""])[0],
            "code_challenge": query.get("code_challenge", [""])[0],
            "scope": query.get("scope", [""])[0],
        }, None

    def authorize_form(self, error: str = ""):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        params, problem = self._authorize_params(query)
        if problem:
            return self.send_json(400, {"error": "invalid_request", "error_description": problem})
        fields = "".join(
            f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">'
            for k, v in params.items()
        )
        err = f'<div class="err">{html.escape(error)}</div>' if error else ""
        return self.send_html(200, LOGIN_PAGE.format(fields=fields, error=err))

    def authorize_submit(self):
        form = self.read_form()
        user_ok = hmac.compare_digest(form.get("username", ""), USERNAME)
        pass_ok = hmac.compare_digest(form.get("password", ""), PASSWORD)
        redirect_uri = form.get("redirect_uri", "")
        if redirect_uri not in REDIRECT_URIS:
            return self.send_json(400, {"error": "invalid_request"})
        if not (user_ok and pass_ok):
            # Re-render the form with the original params preserved.
            self.path = "/authorize?" + urllib.parse.urlencode({
                "client_id": CLIENT_ID,
                "redirect_uri": redirect_uri,
                "state": form.get("state", ""),
                "code_challenge": form.get("code_challenge", ""),
                "code_challenge_method": "S256",
                "scope": form.get("scope", ""),
            })
            return self.authorize_form(error="Wrong username or password.")

        purge_codes()
        code = secrets.token_urlsafe(32)
        CODES[code] = {
            "challenge": form.get("code_challenge", ""),
            "redirect_uri": redirect_uri,
            "scope": form.get("scope", ""),
            "expires": time.time() + CODE_TTL,
        }
        target = redirect_uri + ("&" if "?" in redirect_uri else "?") + urllib.parse.urlencode(
            {"code": code, "state": form.get("state", "")}
        )
        return self.send_empty(302, {"Location": target})

    def _client_authenticated(self, form: dict) -> bool:
        """client_secret_post, client_secret_basic, or public (PKCE-only)."""
        supplied = form.get("client_secret")
        auth_header = self.headers.get("Authorization", "")
        if not supplied and auth_header.lower().startswith("basic "):
            try:
                decoded = base64.b64decode(auth_header[6:]).decode()
                _, _, supplied = decoded.partition(":")
            except (ValueError, UnicodeDecodeError):
                supplied = None
        if supplied is None:
            # Public client: PKCE alone is the proof. Allowed, per OAuth 2.1.
            return True
        return hmac.compare_digest(supplied, CLIENT_SECRET)

    def issue_tokens(self, scope: str) -> dict:
        now = int(time.time())
        access = jwt_encode({
            "iss": ISSUER, "sub": USERNAME, "aud": CLIENT_ID,
            "iat": now, "exp": now + ACCESS_TTL, "scope": scope, "typ": "access",
        })
        refresh = jwt_encode({
            "iss": ISSUER, "sub": USERNAME, "aud": CLIENT_ID,
            "iat": now, "exp": now + REFRESH_TTL, "scope": scope, "typ": "refresh",
        })
        return {
            "access_token": access,
            "token_type": "Bearer",
            "expires_in": ACCESS_TTL,
            "refresh_token": refresh,
            "scope": scope,
        }

    def token(self):
        form = self.read_form()
        if not self._client_authenticated(form):
            return self.send_json(401, {"error": "invalid_client"})

        grant = form.get("grant_type")
        if grant == "refresh_token":
            claims = jwt_decode(form.get("refresh_token", ""))
            if not claims or claims.get("typ") != "refresh":
                return self.send_json(400, {"error": "invalid_grant"})
            return self.send_json(200, self.issue_tokens(claims.get("scope", "")))

        if grant != "authorization_code":
            return self.send_json(400, {"error": "unsupported_grant_type"})

        purge_codes()
        entry = CODES.pop(form.get("code", ""), None)  # single use
        if not entry:
            return self.send_json(400, {"error": "invalid_grant"})
        if form.get("redirect_uri") and form["redirect_uri"] != entry["redirect_uri"]:
            return self.send_json(400, {"error": "invalid_grant"})

        verifier = form.get("code_verifier", "")
        digest = b64url(hashlib.sha256(verifier.encode()).digest())
        if not hmac.compare_digest(digest, entry["challenge"]):
            return self.send_json(400, {"error": "invalid_grant", "error_description": "PKCE failed"})

        return self.send_json(200, self.issue_tokens(entry["scope"]))

    def verify(self):
        """nginx auth_request target: 200 = let it through, 401 = block."""
        auth = self.headers.get("Authorization", "")
        if not auth.lower().startswith("bearer "):
            return self.send_empty(401, {
                "WWW-Authenticate": f'Bearer resource_metadata="{ISSUER}/.well-known/oauth-protected-resource"'
            })
        claims = jwt_decode(auth[7:].strip())
        if not claims or claims.get("typ") != "access":
            return self.send_empty(401, {
                "WWW-Authenticate": f'Bearer error="invalid_token", resource_metadata="{ISSUER}/.well-known/oauth-protected-resource"'
            })
        return self.send_empty(200, {"X-Auth-User": str(claims.get("sub", ""))})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    print(f"oauth-agents auth server on :{port}, issuer {ISSUER}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
