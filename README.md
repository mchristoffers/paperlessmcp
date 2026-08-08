# paperlessmcp — Homeserver Coolify

[PaperlessMCP](https://github.com/barryw/PaperlessMCP) auf der **Homeserver**-Coolify.
Oeffentlich erreichbar ueber den geteilten Homelab-Cloudflare-Tunnel, fuer den
claude.ai Web-Connector (der kann nicht ins Tailnet). PaperlessMCP hat kein
eigenes Login — deshalb sitzt Dex + oauth2-proxy davor, siehe unten. Kein
Tailscale-Pfad mehr.

## Claude Code Plugin

Dieses Repo ist gleichzeitig ein Claude-Code-Plugin (`.claude-plugin/plugin.json`
+ `.mcp.json`) und ein Agent-Plugins-1.0.0-Paket (root `plugin.json` +
`mcp.json`), listet den `paperless-ngx` MCP-Server via
`https://paperlessmcp-oauth.mchristoffers.dev/mcp` (OAuth) und wird ueber die
[mchristoffers/claude-marketplace](https://github.com/mchristoffers/claude-marketplace)
Marketplace installiert. Fuer den lokalen Claude-Code-OAuth-Callback
(`http://localhost:51823/callback`) muss diese Redirect-URI zusaetzlich in
`dex/config.yaml` (`staticClients[0].redirectURIs`) eingetragen sein, plus
`PAPERLESSMCP_DEX_CLIENT_SECRET` lokal exportiert werden (gleicher Wert wie
`DEX_CLIENT_SECRET` in Coolify).

## Zugriff

**`https://paperlessmcp-oauth.mchristoffers.dev/mcp`**

OAuth 2.1 + PKCE, Login via Dex (`moritz`, lokales Passwort). Details:
Dex issued Tokens, oauth2-proxy prueft sie (`skip-jwt-bearer-tokens`-Modus,
API-Client schickt eigenen Bearer-Token statt Cookie-Flow) und reicht dann
zum unveraenderten PaperlessMCP durch. Ein `paperlessmcp-oauth-router`
(Caddy, plain HTTP hinter dem Tunnel — der terminiert TLS) routet `/dex/*`
zu Dex, `/mcp*` zum Gate, und liefert die MCP-Protected-Resource-Metadata
unter `/.well-known/oauth-protected-resource`.

## Stack

`docker-compose.production.yml`:

- `paperlessmcp` — `ghcr.io/barryw/paperlessmcp`, veroeffentlicht keinen
  Host-Port. Haengt direkt im internen Docker-Netz der bestehenden
  `paperless`-App (`nft6hzc8en3d17anjht3vm1f_app_internal`, extern
  referenziert) und spricht Paperless ueber `https://paperless.mchristoffers.dev`
  an — denselben Hostnamen, den auch ein Browser/Tailnet-Client sieht.
- `paperless-alias` — reiner `socat`-TCP-Durchreicher, registriert den Alias
  `paperless.mchristoffers.dev` im geteilten Docker-Netz und leitet Port 443
  an den bestehenden `paperless-proxy` (Caddy, Port 8443) weiter. TLS
  terminiert unveraendert dort mit dessen echtem Zertifikat; hier werden nur
  verschluesselte Bytes durchgereicht. Macht Download-/Vorschau-/
  Thumbnail-URLs, die PaperlessMCP zurueckgibt (`client.BaseUrl` in
  `DocumentTools.cs`), direkt im Browser/Tailnet anklickbar, ohne die
  bestehende `paperless`-App anzufassen.
- `dex` — statisch konfigurierter OIDC-Server (ein Client `claude-mcp`, ein
  lokaler Nutzer `moritz`), memory storage.
- `oauth2-proxy` — validiert Bearer-JWTs gegen Dex' JWKS, reicht durch.
- `paperlessmcp-oauth-router` — Caddy, plain HTTP, Port 8084 (kein LAN-
  Zugriff noetig, nur der Tunnel-Container erreicht ihn).

Der Paperless-API-Token liegt nur als Coolify-Environment-Variable
(`PAPERLESS_API_TOKEN`) vor, generiert fuer den bestehenden `moritz`-Nutzer via
`python manage.py drf_create_token moritz` im laufenden `paperless`-Container.

## Immer aktuell

`image: …:latest` plus `pull_policy: always`. Ein Redeploy zieht damit immer
den neuesten Stand, **Major-Versionen eingeschlossen** — Moritz' ausdrueckliche
Wahl. Die Action laeuft zusaetzlich sonntags 04:15 UTC (15 Minuten nach dem
`paperless`-Update) per `schedule` und aktualisiert von allein.

Kein Rollback vorgesehen; `PAPERLESSMCP_VERSION` existiert trotzdem als
Coolify-Env-Var — im Notfall auf eine Version setzen, redeployen. Kein Backup
noetig: PaperlessMCP haelt keinen eigenen Datenbestand, nur den
`paperlessmcp_outbox`-Volume fuer AI-Exporte, jederzeit aus Paperless neu
erzeugbar.

## Deploy

Push auf `main` → GitHub Action validiert das Compose, signiert das Payload
und POSTet es an Coolifys manuellen GitHub-Webhook, dann wartet sie auf das
Ergebnis. Kein Health-Check auf der URL (Endpoint verlangt jetzt OAuth).
