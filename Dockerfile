FROM dhi.io/python:3.14.8-debian13-dev@sha256:14f7c5536b77465b97a81687e5534a621db86b861d8afa9a49b5802ecd40dcbf AS builder
WORKDIR /build/
COPY --from=dhi.io/uv:0.13.1-debian13-dev@sha256:9589cbb3cc8b11540bf55b12b16d2e5f06a41fec7e340c086ba23c2f8b9a1981 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock /build/
RUN uv sync --frozen --no-dev

FROM dhi.io/python:3.14.8-debian13@sha256:1f47877c0ddcce5f328550286a4b33b0f5e408455e396cd9056b3220a66aad40 AS app
WORKDIR /app/
COPY --from=builder /build/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY main.py /app/
COPY app/ /app/app/
ENTRYPOINT [ "python", "main.py" ]
