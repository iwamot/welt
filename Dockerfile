FROM dhi.io/python:3.15.2-debian13-dev@sha256:57d020a69247e1e3c63e640d1df57633b8ea444498dccda306349c0debbabdc8 AS builder
WORKDIR /build/
COPY --from=dhi.io/uv:0.13.7-debian13-dev@sha256:bce362291f4734cdff1241067b20ba2ed3600e751c3cff32b96e54a005bf4fc3 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock /build/
RUN uv sync --frozen --no-dev

FROM dhi.io/python:3.15.2-debian13@sha256:5f3efbd860ff18cf93c93e5812e300a17e6e06d35be4f973581f3129addeccac AS app
WORKDIR /app/
COPY --from=builder /build/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY main.py /app/
COPY app/ /app/app/
ENTRYPOINT [ "python", "main.py" ]
