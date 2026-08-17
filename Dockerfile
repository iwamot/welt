FROM dhi.io/python:3.14.7-debian13-dev@sha256:7e527362b86f335bf5db8db53ab6b7aa128a0db6c1d4da75c32436424e0255ee AS builder
WORKDIR /build/
COPY --from=dhi.io/uv:0.12.5-debian13-dev@sha256:cb1bb7808999e6b8c3013ef51d4c42f3754ed16edb711d44f45be6a799896310 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock /build/
RUN uv sync --frozen --no-dev

FROM dhi.io/python:3.14.7-debian13@sha256:ca15493305d675cccc8f3ea8ee5cdff5f4904ae8f90ab9fd26a0a5cbe5ad984a AS app
WORKDIR /app/
COPY --from=builder /build/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY main.py /app/
COPY app/ /app/app/
ENTRYPOINT [ "python", "main.py" ]
