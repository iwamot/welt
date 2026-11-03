FROM dhi.io/python:3.15.2-debian13-dev@sha256:614ebfc97307edd14b833f3ef7200f461bb8e887e0658ae5d24022d3e03d7073 AS builder
WORKDIR /build/
COPY --from=dhi.io/uv:0.13.6-debian13-dev@sha256:131dfb28ea9a3fd02dbc802daa9d64b4bc0c5b85ae6956bfc1462584b83be7c3 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock /build/
RUN uv sync --frozen --no-dev

FROM dhi.io/python:3.15.2-debian13@sha256:eab0da10c4231aeaaa5c198dadebe30a050290870cde20b717528e2c574c6c92 AS app
WORKDIR /app/
COPY --from=builder /build/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY main.py /app/
COPY app/ /app/app/
ENTRYPOINT [ "python", "main.py" ]
