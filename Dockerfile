FROM dhi.io/python:3.15.2-debian13-dev@sha256:6d0a6d7eebd4128b68e8f7dfccb3faea663a3083e4b16f86ff0095c6b2823e62 AS builder
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
