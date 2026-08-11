# paperlessmcp — Homeserver Coolify

[PaperlessMCP](https://github.com/barryw/PaperlessMCP) auf der **Homeserver**-Coolify.
Oeffentlich erreichbar ueber den geteilten Homelab-Cloudflare-Tunnel, fuer den
claude.ai Web-Connector (der kann nicht ins Tailnet). PaperlessMCP hat kein
eigenes Login — deshalb sitzt [oauth-agents](https://github.com/mchristoffers/oauth-agents)
davor, siehe unten. Kein Tailscale-Pfad mehr.

## Claude Code Plugin

Dieses Repo ist gleichzeitig ein Claude-Code-Plugin (`.claude-plugin/plugin.json`
+ `.mcp.json`) und ein Agent-Plugins-1.0.0-Paket (root `plugin.json` +
`mcp.json`), listet den `paperless-ngx` MCP-Server via
`https://paperlessmcp-oauth.mchristoffers.dev/mcp` (OAuth) und wird ueber die
[mchristoffers/claude-marketplace](https://github.com/mchristoffers/claude-marketplace)
Marketplace installiert. Nichts einzutragen: `oauth-agents` beantwortet
`POST /register` (Dynamic Client Registration) immer mit demselben statischen
Client, und Loopback-Callbacks auf beliebigem Port sind pauschal erlaubt
(RFC 8252) — Claude Code holt sich Client-ID und Secret also selbst.

## Zugriff

**`https://paperlessmcp-oauth.mchristoffers.dev/mcp`**

OAuth 2.1 + PKCE, ein Login (`GATEWAY_USERNAME`/`GATEWAY_PASSWORD`). Der
`oauth`-Container ist Authorization Server und Gate in einem: er stellt die
Tokens aus, prueft den Bearer am `/mcp`-Pfad und reicht zum unveraenderten
PaperlessMCP durch. Er liefert auch die MCP-Protected-Resource-Metadata unter
`/.well-known/oauth-protected-resource`. Hinter dem Tunnel plain HTTP — TLS
terminiert Cloudflare.

## Stack

`docker-compose.production.yml`:

- `paperlessmcp` — `ghcr.io/barryw/paperlessmcp`, veroeffentlicht keinen
  Host-Port. Haengt direkt im internen Docker-Netz der bestehenden
  `paperless`-App (`nft6hzc8en3d17anjht3vm1f_app_internal`, extern
  referenziert) und spricht Paperless ueber `https://paperless.mchristoffers.dev`
  an — denselben Hostnamen, den auch ein Browser/Tailnet-Client sieht. Das
  braucht keine Hilfskonstruktion im Docker-Netz: oeffentliches DNS liefert die
  Tailscale-VIP, der Host routet Container-Traffic ueber `tailscale0` dorthin,
  und dort haengt `paperless-proxy` mit gueltigem Zertifikat auf 443. Damit
  sind die Download-/Vorschau-/Thumbnail-URLs, die PaperlessMCP baut
  (`client.BaseUrl` in `DocumentTools.cs`), direkt anklickbar.
- `oauth` — `ghcr.io/mchristoffers/oauth-agents`, Port 8084 (kein LAN-Zugriff
  noetig, nur der Tunnel-Container erreicht ihn). Volume `oauth_data:/data`
  haelt den Signing-Key, sonst logged jeder Redeploy alle Clients aus.

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
