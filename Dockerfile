FROM dhi.io/python:3.15.2-debian13-dev@sha256:3ba5300d40d7b20ed83d4968a66dc1e92d3c53bc86c5305eec3a08cf85165ee3 AS builder
WORKDIR /build/
COPY --from=dhi.io/uv:0.13.9-debian13-dev@sha256:b1368960e22a8261508bde6206b432f5a6c4113861add364ebf6acee41d9ae50 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock /build/
RUN uv sync --frozen --no-dev

FROM dhi.io/python:3.15.2-debian13@sha256:fc5c71d66de243d9026d7a2149f51221db8493ed49a391a0d5543622339de60e AS app
WORKDIR /app/
COPY --from=builder /build/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY main.py /app/
COPY app/ /app/app/
ENTRYPOINT [ "python", "main.py" ]
