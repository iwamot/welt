FROM dhi.io/python:3.15.2-debian13-dev@sha256:bd03dfeeb9fd00ba49e01701fa9014af705c33b1999715ebe3048b8e47c08186 AS builder
WORKDIR /build/
COPY --from=dhi.io/uv:0.13.7-debian13-dev@sha256:050ebca4bfefd9f66547a44fc488fc9e9ed7718362ae8395f87434d805bb7b52 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock /build/
RUN uv sync --frozen --no-dev

FROM dhi.io/python:3.15.2-debian13@sha256:b3902cb62999904c02c54e7942b53e3cbd296eb3dabf9e95406081a857baae4c AS app
WORKDIR /app/
COPY --from=builder /build/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY main.py /app/
COPY app/ /app/app/
ENTRYPOINT [ "python", "main.py" ]
