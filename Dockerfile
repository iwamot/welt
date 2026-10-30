FROM dhi.io/python:3.15.2-debian13-dev@sha256:fe4ba32f4d71f62df9abc9b67fc58cc58f9b75c594200146f01ed682ea35bad3 AS builder
WORKDIR /build/
COPY --from=dhi.io/uv:0.13.6-debian13-dev@sha256:a64074ff9d8b5982c783290b70c7c20b0801e2614fa6b1c9d809c277c489e763 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock /build/
RUN uv sync --frozen --no-dev

FROM dhi.io/python:3.15.2-debian13@sha256:6ac87806e8be54b58675cb919c69d597b2b9c097ecb5cd3725d5a0c14219d693 AS app
WORKDIR /app/
COPY --from=builder /build/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY main.py /app/
COPY app/ /app/app/
ENTRYPOINT [ "python", "main.py" ]
