FROM dhi.io/python:3.15.2-debian13-dev@sha256:83a4f461a928ba2a26002bc31f33f5f5f6247e9d6d90f1b12b5d533ed31fa850 AS builder
WORKDIR /build/
COPY --from=dhi.io/uv:0.13.6-debian13-dev@sha256:72bd7cb5df50c391b6e1b5e5550746ce0c4e5bb417714fb158bd02237f30d72b /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock /build/
RUN uv sync --frozen --no-dev

FROM dhi.io/python:3.15.2-debian13@sha256:d18cc222a379b8caa077ce41db24b77e351934dc4c4966d52225dfe40efa1e61 AS app
WORKDIR /app/
COPY --from=builder /build/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY main.py /app/
COPY app/ /app/app/
ENTRYPOINT [ "python", "main.py" ]
