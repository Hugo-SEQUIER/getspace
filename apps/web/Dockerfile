# syntax=docker/dockerfile:1
# The Space website. Build it from the repository root, because the build also packs the
# agent skills from ../../skills:
#   docker build -f apps/web/Dockerfile -t space-web .
# Nothing secret is needed to build. At run time the container reads SPACE_ACCESS_MODE,
# SPACE_ACCESS_KEY, SPACE_API_URL, SPACE_WEB_URL and SPACE_OWNER_NAME (see .env.example).

FROM node:22.23.3-bookworm-slim AS build
WORKDIR /src/apps/web
# Dependencies first, so a source change reuses this layer. The install hooks copy the PDF.js
# worker and the favicon into public/, so they need those scripts and that file.
COPY apps/web/package.json apps/web/package-lock.json ./
COPY apps/web/scripts ./scripts
COPY apps/web/app/favicon.ico ./app/favicon.ico
RUN npm ci --no-audit --no-fund
COPY skills /src/skills
COPY apps/web ./
RUN npm run build

FROM node:22.23.3-bookworm-slim
ENV NODE_ENV=production HOST=0.0.0.0 PORT=3000
WORKDIR /app
# `npm start` runs scripts/serve.mjs: it serves the built client and the bundled server, and
# needs only its HTTP server package (and that package's hono peer) from node_modules.
COPY --from=build /src/apps/web/package.json ./
COPY --from=build /src/apps/web/scripts/serve.mjs ./scripts/serve.mjs
COPY --from=build /src/apps/web/dist ./dist
COPY --from=build /src/apps/web/node_modules/@hono/node-server ./node_modules/@hono/node-server
COPY --from=build /src/apps/web/node_modules/hono ./node_modules/hono
USER node
EXPOSE 3000
# The same command as `npm start`, without npm in between, so signals reach the server.
CMD ["node", "scripts/serve.mjs"]
