# Test-only fake Chat Completions endpoint. Not used by the shipped image.
# python:3.12-slim-bookworm
FROM python:3.14-slim-bookworm@sha256:48b13b003dda20b16f9442b8475aa05fe21bf6579a8c881db92ffb4d8fd20f83
WORKDIR /app
COPY tests/support/fake_chat_llm.py /app/fake_chat_llm.py
RUN pip install --no-cache-dir "starlette==0.46.2" "uvicorn==0.34.2"
EXPOSE 8080
CMD ["python", "/app/fake_chat_llm.py"]
