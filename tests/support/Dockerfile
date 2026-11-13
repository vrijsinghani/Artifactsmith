# Test-only fake Chat Completions endpoint. Not used by the shipped image.
# python:3.14-slim-trixie
FROM python:3.15-slim-trixie@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928
WORKDIR /app
COPY tests/support/fake_chat_llm.py /app/fake_chat_llm.py
RUN pip install --no-cache-dir "starlette==0.46.2" "uvicorn==0.34.2"
EXPOSE 8080
CMD ["python", "/app/fake_chat_llm.py"]
