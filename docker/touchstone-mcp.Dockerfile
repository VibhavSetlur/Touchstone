# Touchstone MCP server image — slim, multi-stage, non-root.
FROM python:3.14-slim AS builder
WORKDIR /build
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock* ./
COPY packages/ packages/
RUN uv sync --frozen --no-dev --extra all

FROM python:3.14-slim
RUN useradd --uid 1001 --create-home touchstone
USER touchstone
WORKDIR /home/touchstone
COPY --from=builder --chown=touchstone:touchstone /build/.venv /opt/touchstone/.venv
ENV PATH="/opt/touchstone/.venv/bin:$PATH"
ENV TOUCHSTONE_CONFIG=/home/touchstone/.touchstone/config.toml
EXPOSE 8765
ENTRYPOINT ["touchstone-mcp"]
CMD ["--transport", "streamable-http"]
