FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd -r -u 10001 bruce
COPY server.py ./
USER bruce
EXPOSE 8765
CMD ["uvicorn","server:app","--host","127.0.0.1","--port","8765"]
