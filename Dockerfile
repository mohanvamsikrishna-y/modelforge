FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY modelforge ./modelforge
COPY tests ./tests
RUN useradd --create-home runner && chown -R runner:runner /app
USER runner
CMD ["python", "-m", "modelforge", "demo"]
