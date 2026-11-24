FROM dhi.io/python:3.15.2-debian13-dev@sha256:6ae754ee360f846c5def592cbb43946d6e0c4cd3fdb7ced0065aa89ed3d76ac3 AS builder
WORKDIR /build/
COPY --from=dhi.io/uv:0.13.9-debian13-dev@sha256:e5a2eb64936f9738340cfc21b2eed235fa226b3e9197f6d13ff34b503c3735da /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock /build/
RUN uv sync --frozen --no-dev

FROM dhi.io/python:3.15.2-debian13@sha256:76e89381500bad12fdaba7333bca42a96e4f1a72e3bf5ef753bbe5258cc77ac8 AS app
WORKDIR /app/
COPY --from=builder /build/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY main.py /app/
COPY app/ /app/app/
ENTRYPOINT [ "python", "main.py" ]
