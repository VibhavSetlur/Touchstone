# Triage UI image.
FROM node:26-slim AS builder
WORKDIR /build
RUN corepack enable
COPY package.json tsconfig.json next.config.mjs ./
COPY app ./app
COPY components ./components
RUN pnpm install --frozen-lockfile || npm install
RUN npm run build || pnpm build

FROM node:26-slim
RUN useradd --uid 1001 --create-home touchstone
USER touchstone
WORKDIR /home/touchstone/app
COPY --from=builder --chown=touchstone:touchstone /build/.next         ./.next
COPY --from=builder --chown=touchstone:touchstone /build/node_modules  ./node_modules
COPY --from=builder --chown=touchstone:touchstone /build/package.json  ./package.json
EXPOSE 3001
ENV NODE_ENV=production
CMD ["npx", "next", "start", "-p", "3001"]
