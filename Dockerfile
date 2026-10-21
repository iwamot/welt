FROM dhi.io/python:3.14.8-debian13-dev@sha256:f342776dc2e3f2c3d9f37cf4cc182bbeb81b4eb4d3543357163aa5fa95bdc8bd AS builder
WORKDIR /build/
COPY --from=dhi.io/uv:0.13.3-debian13-dev@sha256:538e03cfb921102dafe5a083a8fb4ee5a7758be4170f32788fac191325b0307d /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock /build/
RUN uv sync --frozen --no-dev

FROM dhi.io/python:3.14.8-debian13@sha256:1f47877c0ddcce5f328550286a4b33b0f5e408455e396cd9056b3220a66aad40 AS app
WORKDIR /app/
COPY --from=builder /build/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY main.py /app/
COPY app/ /app/app/
ENTRYPOINT [ "python", "main.py" ]
