FROM python:3.13-slim
WORKDIR /app
ENV OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 PYTHONDONTWRITEBYTECODE=1 MPLCONFIGDIR=/tmp/matplotlib
COPY requirements-runtime.txt ./
RUN pip install --no-cache-dir -r requirements-runtime.txt && useradd --uid 10001 --create-home researcher
COPY *.py ./
COPY static ./static
COPY docs ./docs
COPY README.md LICENSE CITATION.cff SECURITY.md CHANGELOG.md ./
RUN mkdir -p artifacts && chown researcher:researcher artifacts
USER researcher
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--limit-concurrency", "32"]
