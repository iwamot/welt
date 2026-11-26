FROM dhi.io/python:3.15.2-debian13-dev@sha256:a1069444b00f18a15f0088a7f6c33ed6070d7f2593919130892b22c5a4f124b7 AS builder
WORKDIR /build/
COPY --from=dhi.io/uv:0.13.10-debian13-dev@sha256:bcc203a1345c584f4ee5659432b21fc9ab6a6a37e5fe8727a732589252528ed3 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock /build/
RUN uv sync --frozen --no-dev

FROM dhi.io/python:3.15.2-debian13@sha256:d5387cad673412ef60e7fdadec757bd653c7dc289bfdccb4beff6d1ccec94f36 AS app
WORKDIR /app/
COPY --from=builder /build/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY main.py /app/
COPY app/ /app/app/
ENTRYPOINT [ "python", "main.py" ]
