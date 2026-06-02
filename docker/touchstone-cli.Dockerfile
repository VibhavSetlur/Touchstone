# Touchstone CLI image — for use in CI runners.
FROM python:3.12-slim AS builder
WORKDIR /build
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock* ./
COPY packages/ packages/
RUN uv sync --frozen --no-dev --extra all

FROM python:3.12-slim
RUN useradd --uid 1001 --create-home touchstone
USER touchstone
WORKDIR /home/touchstone
COPY --from=builder --chown=touchstone:touchstone /build/.venv /opt/touchstone/.venv
ENV PATH="/opt/touchstone/.venv/bin:$PATH"
ENTRYPOINT ["touchstone"]
CMD ["--help"]
