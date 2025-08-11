# Use an official Python image (works on ARM and x86)
FROM python:3.11-slim

# Set the working directory in the container
WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your app code
COPY . .

# Expose the port your app runs on
EXPOSE 8000

# Set environment variables (optional, for Flask)
ENV FLASK_ENV=production

# Start the app with Gunicorn
CMD ["gunicorn", "-w", "2", "--timeout", "120", "-b", "0.0.0.0:8000", "app:app"]

