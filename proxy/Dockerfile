FROM nginx:alpine
# Baked in, never bind-mounted: Coolify creates a *directory* at a file mount
# point that does not exist yet, and nginx/the app then crash-loops.
COPY nginx.conf /etc/nginx/templates/default.conf.template
