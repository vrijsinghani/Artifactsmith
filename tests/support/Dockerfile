# Test-only fake Chat Completions endpoint. Not used by the shipped image.
# python:3.12-slim-bookworm
FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
WORKDIR /app
COPY tests/support/fake_chat_llm.py /app/fake_chat_llm.py
RUN pip install --no-cache-dir "starlette==0.46.2" "uvicorn==0.34.2"
EXPOSE 8080
CMD ["python", "/app/fake_chat_llm.py"]
