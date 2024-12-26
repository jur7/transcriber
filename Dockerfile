# Dockerfile

# Use an official Python runtime as the base image
FROM python:3.9-slim-buster

# Set the working directory inside the container
WORKDIR /app

# Install ffmpeg for pydub
RUN apt-get update && apt-get install -y ffmpeg

# Copy the backend requirements file into the container
COPY ./backend/requirements.txt /app/

# Install any needed packages specified in requirements.txt, including python-dotenv and pydub
RUN pip install --no-cache-dir -r requirements.txt

# Copy the backend code into the container
COPY ./backend /app/

# Copy the frontend code into the container
COPY ./app /app/app

# Expose the port the app runs on
EXPOSE 5001

# Define environment variable for Flask
ENV FLASK_APP=app.py
ENV FLASK_RUN_HOST=0.0.0.0

# Run the Flask app when the container starts
CMD ["flask", "run", "--port=5001"]