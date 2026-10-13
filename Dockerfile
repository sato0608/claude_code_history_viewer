# 常時稼働サーバー(hub モード)用。環境変数 VIEWER_PASSWORD と PUSH_TOKEN を必ず設定する。
FROM python:3.12-slim
WORKDIR /app
COPY history_parser.py server.py ./
COPY static ./static
ENV DATA_FILE=/data/hub_data.json \
    PYTHONUNBUFFERED=1
RUN mkdir -p /data && useradd --create-home app && chown app /data
USER app
EXPOSE 8765
# PORT はホスティング側が渡す値を使う(無ければ 8765)
CMD ["python", "server.py", "--mode", "hub", "--host", "0.0.0.0"]
