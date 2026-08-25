FROM dhi.io/python:3.14.7-debian13-dev@sha256:bc8161a12cca8610649d851d9af964779875c89c080e166ac41b925a31ea1626 AS builder
WORKDIR /build/
COPY --from=dhi.io/uv:0.12.5-debian13-dev@sha256:c316ce593477d20db1561c0492c29289b04aed127e23c7ff0f6c50bc5f2bafb2 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock /build/
RUN uv sync --frozen --no-dev

FROM dhi.io/python:3.14.7-debian13@sha256:9525354fea02f28b9f51285b8e935807f2a2f98cfa684c5ea0ab1e7addaadf54 AS app
WORKDIR /app/
COPY --from=builder /build/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY main.py /app/
COPY app/ /app/app/
ENTRYPOINT [ "python", "main.py" ]
