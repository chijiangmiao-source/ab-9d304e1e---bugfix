FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    API_HOST=0.0.0.0 \
    API_PORT=8080

WORKDIR /srv

# 纯标准库实现，无第三方依赖，无需 pip install。
COPY app/ ./app/

# 以非 root 用户运行。
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /srv
USER appuser

EXPOSE 8080

# slim 镜像不含 curl，用解释器自身做健康检查。
HEALTHCHECK --interval=10s --timeout=3s --start-period=3s --retries=3 \
    CMD python -c "import json,os,urllib.request,sys; port=os.environ.get('API_PORT','8080'); resp=urllib.request.urlopen('http://127.0.0.1:'+port+'/healthz', timeout=2); sys.exit(0 if json.load(resp).get('status')=='ok' else 1)"

CMD ["python", "-m", "app.server"]
