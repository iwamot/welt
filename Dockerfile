FROM dhi.io/python:3.15.2-debian13-dev@sha256:e341dc2363f0bd99a058683a9bcbf79b994c4670ded4924a099ed9c1a6cf7f54 AS builder
WORKDIR /build/
COPY --from=dhi.io/uv:0.13.10-debian13-dev@sha256:45c7a2c9de53685718f355e179bc4956185a649591d0bbe930b25550d7441b2e /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock /build/
RUN uv sync --frozen --no-dev

FROM dhi.io/python:3.15.2-debian13@sha256:ac18385fab4463424ca98f364a8a5b904271dbf681f1c06b14b191108b4080ae AS app
WORKDIR /app/
COPY --from=builder /build/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY main.py /app/
COPY app/ /app/app/
ENTRYPOINT [ "python", "main.py" ]
