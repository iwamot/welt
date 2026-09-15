FROM dhi.io/python:3.14.7-debian13-dev@sha256:163babeca6942d098d8fc891223485636c8da5ad49e615c6f5bafd70d75677c7 AS builder
WORKDIR /build/
COPY --from=dhi.io/uv:0.12.13-debian13-dev@sha256:1015d156849d2cbe1bfb33e349812268339a658a9bf057cc61cc78934ef90c45 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock /build/
RUN uv sync --frozen --no-dev

FROM dhi.io/python:3.14.7-debian13@sha256:5e1e7ddf4efddd05e414390978128730799d4150738c75060364228a073741ec AS app
WORKDIR /app/
COPY --from=builder /build/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY main.py /app/
COPY app/ /app/app/
ENTRYPOINT [ "python", "main.py" ]
