FROM dhi.io/python:3.15.2-debian13-dev@sha256:47fd20269d6e3e7ebfd75c74c1afc9edb6c0faf409cf4194d158a8dad57852a9 AS builder
WORKDIR /build/
COPY --from=dhi.io/uv:0.13.7-debian13-dev@sha256:6724ead1daca9da46a475fefb1a58ad9a3f91f547d5f5002e855f42c531a0556 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock /build/
RUN uv sync --frozen --no-dev

FROM dhi.io/python:3.15.2-debian13@sha256:3d50888877d6ceecfdfe2b0f8ee52ccbdf0074a43ca7af0d39232ec79974e6ea AS app
WORKDIR /app/
COPY --from=builder /build/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY main.py /app/
COPY app/ /app/app/
ENTRYPOINT [ "python", "main.py" ]
