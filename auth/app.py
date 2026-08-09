"""OAuth 2.1 in front of something that has no login.

Everything is configured through environment variables — there is no config
file. One static client, one user, authorization-code + PKCE, HS256 JWTs.

  POST /register   always returns the one static client (fake DCR)
  GET  /authorize  login form      POST /authorize  check password, issue code
  POST /token      code -> tokens  GET  /verify     nginx auth_request target
  GET  /.well-known/oauth-authorization-server  (+ openid-configuration)
  GET  /.well-known/oauth-protected-resource
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

ISSUER = os.environ["ISSUER"].rstrip("/")
USERNAME = os.environ["USERNAME"]
PASSWORD = os.environ["PASSWORD"]
JWT_SECRET = os.environ["JWT_SECRET"]
CLIENT_SECRET = os.environ["CLIENT_SECRET"]
CLIENT_ID = os.environ.get("CLIENT_ID", "oauth-agents")
# What clients are told they are authenticating *to*. Defaults to the issuer;
# set it when the protected thing lives at a sub-path (e.g. .../mcp).
RESOURCE = os.environ.get("RESOURCE") or ISSUER

ACCESS_TTL = int(os.environ.get("ACCESS_TOKEN_TTL", "3600"))
REFRESH_TTL = int(os.environ.get("REFRESH_TOKEN_TTL", str(30 * 24 * 3600)))

# Extra redirect URIs, comma separated. Loopback (any port) is always allowed
# per RFC 8252, which is what CLI clients need; claude.ai's fixed callback is
# included by default so the connector works out of the box.
REDIRECT_URIS = [u.strip() for u in os.environ.get(
    "REDIRECT_URIS", "https://claude.ai/api/mcp/auth_callback").split(",") if u.strip()]

CODES = {}
CODE_TTL = 300


def redirect_ok(uri: str) -> bool:
    if uri in REDIRECT_URIS:
        return True
    parts = urllib.parse.urlparse(uri)
    return parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1")


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def b64url_decode(txt: str) -> bytes:
    return base64.urlsafe_b64decode(txt + "=" * (-len(txt) % 4))


def jwt_encode(claims: dict) -> str:
    head = b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = b64url(json.dumps(claims, separators=(",", ":")).encode())
    sig = hmac.new(JWT_SECRET.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{b64url(sig)}"


def jwt_decode(token: str) -> dict | None:
    try:
        head, body, sig = token.split(".")
    except ValueError:
        return None
    expected = hmac.new(JWT_SECRET.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(expected, b64url_decode(sig)):
        return None
    try:
        claims = json.loads(b64url_decode(body))
    except ValueError:
        return None
    if claims.get("exp", 0) < time.time() or claims.get("iss") != ISSUER:
        return None
    return claims


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
  <label>Username</label><input name="username" autofocus autocomplete="username">
  <label>Password</label><input name="password" type="password" autocomplete="current-password">
  <button type="submit">Sign in</button>
  {error}
</form>
"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "oauth-agents"

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} {fmt % args}", flush=True)

    def send_json(self, status, payload):
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def send_html(self, status, body):
        raw = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def send_empty(self, status, headers=None):
        self.send_response(status)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def read_form(self):
        n = int(self.headers.get("Content-Length") or 0)
        return {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(n).decode() if n else "").items()}

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/.well-known/oauth-authorization-server", "/.well-known/openid-configuration"):
            return self.send_json(200, {
                "issuer": ISSUER,
                "authorization_endpoint": f"{ISSUER}/authorize",
                "token_endpoint": f"{ISSUER}/token",
                "registration_endpoint": f"{ISSUER}/register",
                "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported":
                    ["client_secret_post", "client_secret_basic", "none"],
                "scopes_supported": ["openid", "offline_access"],
            })
        if path.startswith("/.well-known/oauth-protected-resource"):
            return self.send_json(200, {"resource": RESOURCE, "authorization_servers": [ISSUER]})
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
            n = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(n)  # the client's metadata is deliberately ignored
            return self.send_json(201, {
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "client_id_issued_at": 0,
                "client_secret_expires_at": 0,
                "redirect_uris": REDIRECT_URIS,
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "client_secret_post",
            })
        if path == "/authorize":
            return self.authorize_submit()
        if path == "/token":
            return self.token()
        return self.send_json(404, {"error": "not_found"})

    def authorize_form(self, error=""):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        redirect_uri = q.get("redirect_uri", [""])[0]
        challenge = q.get("code_challenge", [""])[0]
        if not redirect_ok(redirect_uri):
            return self.send_json(400, {"error": "invalid_request",
                                        "error_description": "redirect_uri not allowed"})
        if not challenge or q.get("code_challenge_method", ["S256"])[0] != "S256":
            return self.send_json(400, {"error": "invalid_request",
                                        "error_description": "PKCE (S256) required"})
        params = {"redirect_uri": redirect_uri, "state": q.get("state", [""])[0],
                  "code_challenge": challenge, "scope": q.get("scope", [""])[0]}
        fields = "".join(f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">'
                         for k, v in params.items())
        err = f'<div class="err">{html.escape(error)}</div>' if error else ""
        return self.send_html(200, LOGIN_PAGE.format(fields=fields, error=err))

    def authorize_submit(self):
        form = self.read_form()
        redirect_uri = form.get("redirect_uri", "")
        if not redirect_ok(redirect_uri):
            return self.send_json(400, {"error": "invalid_request"})
        ok = (hmac.compare_digest(form.get("username", ""), USERNAME)
              and hmac.compare_digest(form.get("password", ""), PASSWORD))
        if not ok:
            self.path = "/authorize?" + urllib.parse.urlencode({
                "redirect_uri": redirect_uri, "state": form.get("state", ""),
                "code_challenge": form.get("code_challenge", ""),
                "code_challenge_method": "S256", "scope": form.get("scope", "")})
            return self.authorize_form(error="Wrong username or password.")

        now = time.time()
        for c, e in list(CODES.items()):
            if e["expires"] < now:
                del CODES[c]
        code = secrets.token_urlsafe(32)
        CODES[code] = {"challenge": form.get("code_challenge", ""),
                       "redirect_uri": redirect_uri, "scope": form.get("scope", ""),
                       "expires": now + CODE_TTL}
        sep = "&" if "?" in redirect_uri else "?"
        target = redirect_uri + sep + urllib.parse.urlencode(
            {"code": code, "state": form.get("state", "")})
        return self.send_empty(302, {"Location": target})

    def issue(self, scope):
        now = int(time.time())
        base = {"iss": ISSUER, "sub": USERNAME, "aud": CLIENT_ID, "iat": now, "scope": scope}
        return {
            "access_token": jwt_encode({**base, "exp": now + ACCESS_TTL, "typ": "access"}),
            "token_type": "Bearer",
            "expires_in": ACCESS_TTL,
            "refresh_token": jwt_encode({**base, "exp": now + REFRESH_TTL, "typ": "refresh"}),
            "scope": scope,
        }

    def token(self):
        form = self.read_form()
        supplied = form.get("client_secret")
        auth = self.headers.get("Authorization", "")
        if not supplied and auth.lower().startswith("basic "):
            try:
                _, _, supplied = base64.b64decode(auth[6:]).decode().partition(":")
            except (ValueError, UnicodeDecodeError):
                supplied = None
        # A public client proves itself with PKCE alone, which OAuth 2.1 allows.
        if supplied is not None and not hmac.compare_digest(supplied, CLIENT_SECRET):
            return self.send_json(401, {"error": "invalid_client"})

        if form.get("grant_type") == "refresh_token":
            claims = jwt_decode(form.get("refresh_token", ""))
            if not claims or claims.get("typ") != "refresh":
                return self.send_json(400, {"error": "invalid_grant"})
            return self.send_json(200, self.issue(claims.get("scope", "")))

        if form.get("grant_type") != "authorization_code":
            return self.send_json(400, {"error": "unsupported_grant_type"})

        entry = CODES.pop(form.get("code", ""), None)  # single use
        if not entry or entry["expires"] < time.time():
            return self.send_json(400, {"error": "invalid_grant"})
        if form.get("redirect_uri") and form["redirect_uri"] != entry["redirect_uri"]:
            return self.send_json(400, {"error": "invalid_grant"})
        digest = b64url(hashlib.sha256(form.get("code_verifier", "").encode()).digest())
        if not hmac.compare_digest(digest, entry["challenge"]):
            return self.send_json(400, {"error": "invalid_grant",
                                        "error_description": "PKCE failed"})
        return self.send_json(200, self.issue(entry["scope"]))

    def verify(self):
        challenge = (f'Bearer resource_metadata='
                     f'"{ISSUER}/.well-known/oauth-protected-resource"')
        auth = self.headers.get("Authorization", "")
        if not auth.lower().startswith("bearer "):
            return self.send_empty(401, {"WWW-Authenticate": challenge})
        claims = jwt_decode(auth[7:].strip())
        if not claims or claims.get("typ") != "access":
            return self.send_empty(401, {"WWW-Authenticate": challenge})
        return self.send_empty(200, {"X-Auth-User": str(claims.get("sub", ""))})


if __name__ == "__main__":
    print(f"oauth-agents on :8000, issuer {ISSUER}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
