FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# docker run -e QUIZ_BOT_TOKEN=... -e QUIZ_DEMO_MODE=1 ...
CMD ["python", "bot.py"]
