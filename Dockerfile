FROM dhi.io/python:3.15.2-debian13-dev@sha256:646b70e80c246911c8e31f732a8ba0c014c90abef5a1459a9c9f8a2ca0227bb1 AS builder
WORKDIR /build/
COPY --from=dhi.io/uv:0.13.7-debian13-dev@sha256:9498b4fafdf893784f7345bf79f918a7c909648a22487287932d94a05b18d0eb /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock /build/
RUN uv sync --frozen --no-dev

FROM dhi.io/python:3.15.2-debian13@sha256:d90203ad5dd6c0d9fe58efd6347795375b3f5f2430be2bf61573510452f718e5 AS app
WORKDIR /app/
COPY --from=builder /build/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY main.py /app/
COPY app/ /app/app/
ENTRYPOINT [ "python", "main.py" ]
