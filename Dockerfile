FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements-desk.lock pyproject.toml LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir -r requirements-desk.lock && pip install --no-cache-dir --no-deps . \
    && useradd --uid 10001 --create-home desk && install -d -o desk -g desk -m 700 /data
USER 10001:10001
VOLUME ["/data"]
EXPOSE 8766
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8766/health', timeout=2)"
ENTRYPOINT ["python", "-m", "agentlab.desk"]
CMD ["serve", "--data-dir", "/data", "--host", "0.0.0.0"]
