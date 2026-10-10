# Test-only fake Chat Completions endpoint. Not used by the shipped image.
# python:3.14-slim-trixie
FROM python:3.14-slim-trixie@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170
WORKDIR /app
COPY tests/support/fake_chat_llm.py /app/fake_chat_llm.py
RUN pip install --no-cache-dir "starlette==0.46.2" "uvicorn==0.34.2"
EXPOSE 8080
CMD ["python", "/app/fake_chat_llm.py"]
