FROM dhi.io/python:3.15.2-debian13-dev@sha256:ed4eaeaff74b56287de99ce20dad66192444da7b0924da7c91cf5139a93e10cb AS builder
WORKDIR /build/
COPY --from=dhi.io/uv:0.13.9-debian13-dev@sha256:56f94ca0d240c24f79e196c3cdb746a6826ee9809b3c3cdeeca840f33da886f2 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock /build/
RUN uv sync --frozen --no-dev

FROM dhi.io/python:3.15.2-debian13@sha256:d865291cf12dd5984f92656eacdaabda6e6621872e49995fa0dbd0dbe4a87592 AS app
WORKDIR /app/
COPY --from=builder /build/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY main.py /app/
COPY app/ /app/app/
ENTRYPOINT [ "python", "main.py" ]
