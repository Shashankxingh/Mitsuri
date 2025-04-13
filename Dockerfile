FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy all files to the container
COPY . .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Run your bot script
CMD ["python", "mitsuri_bot.py"]
