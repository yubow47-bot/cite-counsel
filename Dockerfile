FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 CITECOUNSEL_HOST=0.0.0.0
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8001
CMD ["python", "run_chatbox.py"]
