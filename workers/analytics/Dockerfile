# Release builds pass a reviewed image digest as PYTHON_BASE.
ARG PYTHON_BASE=python:3.12-slim
FROM ${PYTHON_BASE}
ENV PYTHONUNBUFFERED=1 MPLBACKEND=Agg OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
WORKDIR /app
COPY . /app
RUN python -m pip install --no-cache-dir .
RUN if [ -f requirements/analytics.txt ]; then python -m pip install --no-cache-dir -r requirements/analytics.txt; fi
RUN useradd --create-home worker && chown -R worker:worker /app
USER worker
CMD ["python", "-m", "stock_machine.integrations.workers"]
