FROM caddy:alpine
COPY Caddyfile /etc/caddy/Caddyfile
RUN caddy adapt --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null
