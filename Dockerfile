FROM dhi.io/python:3.15.2-debian13-dev@sha256:3ba5300d40d7b20ed83d4968a66dc1e92d3c53bc86c5305eec3a08cf85165ee3 AS builder
WORKDIR /build/
COPY --from=dhi.io/uv:0.13.8-debian13-dev@sha256:27e7f1befe60ea0e504eae96a0bdf8f5035423181e75f6596074787661f33a48 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock /build/
RUN uv sync --frozen --no-dev

FROM dhi.io/python:3.15.2-debian13@sha256:5f3efbd860ff18cf93c93e5812e300a17e6e06d35be4f973581f3129addeccac AS app
WORKDIR /app/
COPY --from=builder /build/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY main.py /app/
COPY app/ /app/app/
ENTRYPOINT [ "python", "main.py" ]
