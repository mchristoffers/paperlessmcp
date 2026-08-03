# paperlessmcp — Homeserver Coolify

[PaperlessMCP](https://github.com/barryw/PaperlessMCP) auf der **Homeserver**-Coolify.
Interner Dienst: kein Cloudflare Tunnel, kein Cloudflare Access, keine
oeffentliche Erreichbarkeit. Der Server hat kein eigenes Login — er reicht nur
den Paperless-API-Token weiter — deshalb ausschliesslich intern.

## Zugriff

**`https://paperlessmcp.mchristoffers.dev/mcp`**

Der eigene Name zeigt auf die TailVIP von `svc:paperlessmcp` und ist deshalb
ausschliesslich im Tailnet erreichbar. Tailscale leitet TCP/443 an einen
kleinen Caddy-Sidecar weiter; Caddy holt und erneuert das Let's-Encrypt-
Zertifikat per Cloudflare-DNS-Challenge und proxyt danach intern zu
PaperlessMCP. Kein Cloudflare Tunnel, kein Access, kein Funnel. Außerhalb des
Tailnets ist die TailVIP nicht routbar.

## Stack

`docker-compose.production.yml`:

- `paperlessmcp` — `ghcr.io/barryw/paperlessmcp`, veroeffentlicht keinen
  Host-Port. Haengt direkt im internen Docker-Netz der bestehenden
  `paperless`-App (`nft6hzc8en3d17anjht3vm1f_app_internal`, extern
  referenziert) und spricht Paperless ueber `http://paperless:8000` an — kein
  Umweg ueber Caddy/Tailscale fuer reinen API-Traffic.
- `paperlessmcp-proxy` — eigener minimaler Caddy-Build mit
  Cloudflare-DNS-Modul, bindet nur `127.0.0.1:8444`, das ausschliesslich
  tailscaled als Backend verwendet.

**Bekannte Einschraenkung:** `PAPERLESS_BASE_URL` zeigt bewusst auf die
interne Docker-Adresse `http://paperless:8000`, nicht auf
`https://paperless.mchristoffers.dev`. Alle API-Aufrufe (Suche, Tags,
Upload, …) funktionieren damit einwandfrei. Download-/Vorschau-/Thumbnail-URLs,
die manche Tools zurueckgeben, werden aber aus dieser Basis-URL gebaut und sind
deshalb selbst nur aus dem `paperless`-Docker-Netz erreichbar, nicht direkt aus
dem Browser/Tailnet anklickbar. Falls das stoert: mit Moritz absprechen, ob
stattdessen die externe URL verwendet werden soll (kostet mehr bewegliche
Teile, siehe Commit-Historie/OKF-Notiz).

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
Ergebnis. Kein Health-Check auf der URL — ein GitHub-Runner kommt nicht ins
Tailnet.
