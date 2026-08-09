FROM caddy:alpine
COPY Caddyfile /etc/caddy/Caddyfile
RUN setcap -r /usr/bin/caddy
RUN caddy adapt --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null
