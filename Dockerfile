FROM dhi.io/python:3.15.2-debian13-dev@sha256:57d020a69247e1e3c63e640d1df57633b8ea444498dccda306349c0debbabdc8 AS builder
WORKDIR /build/
COPY --from=dhi.io/uv:0.13.8-debian13-dev@sha256:d3f1d689b5521e1b3dcc90c2564a93219b82ffda2626010ab322a1ab40c63ade /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock /build/
RUN uv sync --frozen --no-dev

FROM dhi.io/python:3.15.2-debian13@sha256:5f3efbd860ff18cf93c93e5812e300a17e6e06d35be4f973581f3129addeccac AS app
WORKDIR /app/
COPY --from=builder /build/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY main.py /app/
COPY app/ /app/app/
ENTRYPOINT [ "python", "main.py" ]
