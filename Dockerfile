# Builds ONLY the Pyrogram monitoring bot (the Base44 web app is ignored by
# this Docker image and continues hosting separately on Base44).
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install dependencies first for better layer caching
COPY bot/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the bot source only
COPY bot/ /app/

# Long-running service
CMD ["python", "bot.py"]
