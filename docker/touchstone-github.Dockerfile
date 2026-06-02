# Touchstone GitHub App — bundles the Python CLI + the TS app together so it
# can spawn `touchstone` locally without a network round-trip.

FROM python:3.12-slim AS pybuilder
WORKDIR /build
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock* ./
COPY packages/ packages/
RUN uv sync --frozen --no-dev --extra all

FROM node:26-slim AS jsbuilder
WORKDIR /build
RUN corepack enable
COPY packages/touchstone-github/package.json packages/touchstone-github/tsconfig.json ./
RUN pnpm install --frozen-lockfile || npm install
COPY packages/touchstone-github/src ./src
RUN npm run build || pnpm build

FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --uid 1001 --create-home touchstone
USER touchstone
WORKDIR /home/touchstone
COPY --from=pybuilder --chown=touchstone:touchstone /build/.venv /opt/touchstone/.venv
COPY --from=jsbuilder  --chown=touchstone:touchstone /build/dist          /home/touchstone/app/dist
COPY --from=jsbuilder  --chown=touchstone:touchstone /build/node_modules  /home/touchstone/app/node_modules
COPY --from=jsbuilder  --chown=touchstone:touchstone /build/package.json  /home/touchstone/app/package.json
ENV PATH="/opt/touchstone/.venv/bin:$PATH"
ENV TOUCHSTONE_CLI=touchstone
WORKDIR /home/touchstone/app
EXPOSE 3000
ENTRYPOINT ["node", "dist/index.js"]
