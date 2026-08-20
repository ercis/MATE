# syntax=docker/dockerfile:1.7
FROM node:22.18.0-alpine AS base
# Install pnpm directly via npm instead of corepack: pinned older Node images
# ship a corepack with stale signature keys / a vm bug that breaks `pnpm install`.
RUN npm install -g pnpm@latest
WORKDIR /repo

# Bring in workspace metadata so pnpm can build the dependency graph.
FROM base AS deps
COPY pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/package.json
COPY packages/module-sdk-ts/package.json packages/module-sdk-ts/package.json
COPY packages/shared-types/package.json packages/shared-types/package.json
RUN pnpm install --frozen-lockfile=false --filter "@mate/web..."

FROM base AS builder
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
ENV OUTPUT_FILE_TRACING_ROOT=/repo
COPY --from=deps /repo/node_modules /repo/node_modules
COPY --from=deps /repo/apps/web/node_modules /repo/apps/web/node_modules
COPY pnpm-workspace.yaml ./
COPY apps/web /repo/apps/web
COPY packages/module-sdk-ts /repo/packages/module-sdk-ts
COPY packages/shared-types /repo/packages/shared-types
# Modules ship their own frontend panels under modules/<id>/panel/ which the
# web app imports via the `@modules/*` alias. Copy them in so webpack can
# resolve those imports at build time, and recreate the host's
# `modules/node_modules` symlink so TypeScript can find type declarations
# for react / next / etc. when type-checking files under modules/.
COPY modules /repo/modules
RUN ln -s ../apps/web/node_modules /repo/modules/node_modules
WORKDIR /repo/apps/web
RUN pnpm build

# Runtime: copy only the standalone bundle. Next's standalone output already
# bundles its required node_modules.
FROM node:22.18.0-alpine AS runner
ENV NODE_ENV=production
ENV PORT=3000
WORKDIR /app

COPY --from=builder /repo/apps/web/.next/standalone /app/
COPY --from=builder /repo/apps/web/.next/static /app/apps/web/.next/static
COPY --from=builder /repo/apps/web/public /app/apps/web/public

# Module bundler runs at container start to esbuild any modules under the
# host-mounted /app/modules into modules/<id>/.dist/ (§5.4). Next's
# standalone output is a tightly traced tree we don't want to perturb, so
# install the bundler in its own /opt/ff-bundler/ dir with its own
# node_modules. The script picks up MODULES_DIR + RUNTIME_EXTERNALS via env.
#
# The bundler must also resolve any npm dep a module panel *bundles* (i.e. is
# NOT in runtime-externals.json). Today that's only bpmn-js/bpmn-auto-layout/
# diagram-js-minimap, which the discovery panel bundles deliberately (Turbopack
# mis-bundles bpmn-moddle – see modules/discovery/.../BpmnCanvas.tsx). These
# are client-only and never enter Next's standalone trace, so they must be
# installed here. Keep versions in sync with apps/web/package.json.
RUN mkdir -p /opt/ff-bundler \
    && cd /opt/ff-bundler \
    && printf '{"name":"ff-bundler","private":true,"type":"module","dependencies":{"esbuild":"^0.24","yaml":"^2","bpmn-js":"^18.16.1","bpmn-auto-layout":"^1.3.0","diagram-js-minimap":"^5.3.0"}}\n' > package.json \
    && npm install --no-fund --no-audit
COPY apps/web/scripts/bundle-modules.mjs /opt/ff-bundler/bundle-modules.mjs
COPY apps/web/lib/runtime-externals.json /opt/ff-bundler/runtime-externals.json
ENV FF_MODULES_DIR=/app/modules \
    FF_RUNTIME_EXTERNALS=/opt/ff-bundler/runtime-externals.json

EXPOSE 3000
HEALTHCHECK --interval=10s --timeout=3s --retries=5 --start-period=5s \
  CMD wget -q --spider http://localhost:3000/processes || exit 1

# Bundle all installed modules, then hand off to Next's standalone server.
# Use `;` not `&&`: a single module that fails to bundle must NOT block the web
# server from booting (that turned one bad panel into a container crash-loop).
# The bundler logs failures loudly; only the affected module's panel is lost,
# the platform still comes up.
CMD ["sh", "-c", "node /opt/ff-bundler/bundle-modules.mjs; node /app/apps/web/server.js"]
