# Stage 1: Build the React SPA
FROM node:26.11-alpine AS build

# Upgrade OS packages first: a pinned base tag can ship a CRITICAL whose fix
# is already in the Alpine repos. A build reuses this layer from its cache, so
# it never sees a fix published later; raising OS_PACKAGES_REFRESHED rebuilds
# it (docs/ops/deploy.md, "Refreshing OS packages").
ARG OS_PACKAGES_REFRESHED=2026-10-28
RUN echo "OS packages as of ${OS_PACKAGES_REFRESHED}" && apk upgrade --no-cache

# RAV-1058: the API key is NOT baked into the bundle. Vite inlines VITE_*
# values at build time, which would ship the secret to every browser — so the
# dashboard now prompts for the key at runtime (AuthGate) and stores it in
# localStorage. Nothing to inject here.

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# Stage 2: Serve with nginx
FROM nginx:1.31-alpine

# Upgrade OS packages first — see the build stage note.
ARG OS_PACKAGES_REFRESHED=2026-10-28
RUN echo "OS packages as of ${OS_PACKAGES_REFRESHED}" && apk upgrade --no-cache

# Replace default nginx config with ours: the LAN vhost.
COPY nginx.conf /etc/nginx/conf.d/default.conf

# The public dashboard vhost is rendered at container start, by the stock
# entrypoint running every executable *.sh in /docker-entrypoint.d/. With
# DASHBOARD_PUBLIC_HOSTNAME unset it renders nothing. The script writes into
# conf.d, which is why conf.d is owned by the nginx user below.
COPY nginx/dashboard-public.conf.template /etc/nginx/dashboard-public/dashboard-public.conf.template
COPY nginx/render-dashboard-public.sh /docker-entrypoint.d/40-dashboard-public-vhost.sh

# Copy built SPA from build stage
COPY --from=build /app/dist /usr/share/nginx/html

# Grant nginx user ownership of directories it needs at runtime
RUN chmod 0755 /docker-entrypoint.d/40-dashboard-public-vhost.sh \
    && chown -R nginx:nginx /var/cache/nginx /var/log/nginx /etc/nginx/conf.d \
    && touch /var/run/nginx.pid && chown nginx:nginx /var/run/nginx.pid

USER nginx

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
