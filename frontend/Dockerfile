# Stage 1: Build the React SPA
FROM node:20.18-alpine AS build

# VITE_* values are inlined into the JS bundle by Vite at build time. The
# compose stack passes this from `${VITE_API_KEY:-${API_KEY:-}}` so a single
# API_KEY in the root .env covers both backend auth and frontend bundling
# without a separate frontend/.env file. Empty is fine in dev (the backend
# short-circuits auth when API_KEY is empty).
ARG VITE_API_KEY=""
ENV VITE_API_KEY=$VITE_API_KEY

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# Stage 2: Serve with nginx
FROM nginx:1.27-alpine

# Remove default nginx config
RUN rm /etc/nginx/conf.d/default.conf

# Copy custom nginx config
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy built SPA from build stage
COPY --from=build /app/dist /usr/share/nginx/html

# Grant nginx user ownership of directories it needs at runtime
RUN chown -R nginx:nginx /var/cache/nginx /var/log/nginx /etc/nginx/conf.d \
    && touch /var/run/nginx.pid && chown nginx:nginx /var/run/nginx.pid

USER nginx

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
