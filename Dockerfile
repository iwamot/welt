FROM dhi.io/python:3.15.2-debian13-dev@sha256:281b99000717552218659ee210bd5432330c8e6d679935f5458530cb89b9c10a AS builder
WORKDIR /build/
COPY --from=dhi.io/uv:0.13.9-debian13-dev@sha256:71aa3929d3a5ee01c906ce3e300a15f7c247a301ce81f9a2cd4cb2c9d6762e91 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock /build/
RUN uv sync --frozen --no-dev

FROM dhi.io/python:3.15.2-debian13@sha256:a85031f7d1403e0d66b427651a528d2d338438cd9c5017b17481df50978eda53 AS app
WORKDIR /app/
COPY --from=builder /build/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY main.py /app/
COPY app/ /app/app/
ENTRYPOINT [ "python", "main.py" ]
