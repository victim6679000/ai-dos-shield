FROM python:3.11-slim
WORKDIR /app
ENV PIP_NO_CACHE_DIR=1 HF_HOME=/app/.hf_cache PYTHONUNBUFFERED=1
COPY requirements.txt .
# CPU-only torch. The default wheel pulls ~2GB of CUDA libraries we cannot use.
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch==2.5.1 \
 && pip install -r requirements.txt
COPY app.py .
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=180s --retries=10 \
  CMD python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)"
CMD ["uvicorn","app:app","--host","0.0.0.0","--port","8000"]
