FROM dhi.io/python:3.15.2-debian13-dev@sha256:3ba5300d40d7b20ed83d4968a66dc1e92d3c53bc86c5305eec3a08cf85165ee3 AS builder
WORKDIR /build/
COPY --from=dhi.io/uv:0.13.8-debian13-dev@sha256:4d7b78533cefd5e5567bef51161e5cdf897499d149afc6c4c047c18608fb8fe9 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock /build/
RUN uv sync --frozen --no-dev

FROM dhi.io/python:3.15.2-debian13@sha256:5f3efbd860ff18cf93c93e5812e300a17e6e06d35be4f973581f3129addeccac AS app
WORKDIR /app/
COPY --from=builder /build/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY main.py /app/
COPY app/ /app/app/
ENTRYPOINT [ "python", "main.py" ]
