FROM dhi.io/python:3.15.2-debian13-dev@sha256:c73e2cb9e8d56cfab7f01b99f67b2c830e84e7397dfe666d8bfaf5a69ad0ce85 AS builder
WORKDIR /build/
COPY --from=dhi.io/uv:0.13.9-debian13-dev@sha256:c9b0c17d4efe26a98bcbd8597c202a4c7f76774aa479ba42f5f327322c42a547 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock /build/
RUN uv sync --frozen --no-dev

FROM dhi.io/python:3.15.2-debian13@sha256:c6c9414d78bd285be1a4dacfb94ae61732c77019605b1678792823e319b720f6 AS app
WORKDIR /app/
COPY --from=builder /build/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY main.py /app/
COPY app/ /app/app/
ENTRYPOINT [ "python", "main.py" ]
