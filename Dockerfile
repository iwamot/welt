FROM dhi.io/python:3.14.8-debian13-dev@sha256:0fc1a06a500fd1a1a884d7bb53c8da242527878e39eefcf106ed8050e94693ad AS builder
WORKDIR /build/
COPY --from=dhi.io/uv:0.13.4-debian13-dev@sha256:34076b552f25c0cd27ead5ddeb0ec82addc1ac5e8ab5d5ccf91bc6baa1528739 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock /build/
RUN uv sync --frozen --no-dev

FROM dhi.io/python:3.14.8-debian13@sha256:36c38f4be040b095f67fbb0982d18875518f1b58d23b9ad3cc8bc903a7f7524e AS app
WORKDIR /app/
COPY --from=builder /build/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY main.py /app/
COPY app/ /app/app/
ENTRYPOINT [ "python", "main.py" ]
