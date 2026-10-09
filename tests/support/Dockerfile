# Test-only Chat Completions stand-in. Not used by the shipped image.
FROM python:3.12-slim-bookworm
WORKDIR /app
COPY tests/support/mock_llm.py /app/mock_llm.py
RUN pip install --no-cache-dir "starlette>=0.38" "uvicorn>=0.30"
EXPOSE 8080
CMD ["python", "/app/mock_llm.py"]
