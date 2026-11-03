FROM dhi.io/python:3.15.2-debian13-dev@sha256:74f0261e2bfead93cb7b7c553b198ce2a7b8ddeae8e35264043e1864379976ac AS builder
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
