# Audio Transcriber

Audio Transcriber is an audio transcription application with a user-friendly web interface. It allows you to upload audio files and get transcriptions using either AssemblyAI or OpenAI's Whisper API. The application automatically handles large files by splitting them into manageable chunks.

## Features

- **User-Friendly Web Interface:** Built using HTML, CSS, and JavaScript for a clean and intuitive experience.
- **Multiple Transcription APIs:** Supports both AssemblyAI and OpenAI Whisper for flexibility.
- **Language Selection:** Choose your audio’s language manually or use the automatic language detection for convenience.
- **Transcription History:** View, copy, download, or delete previously transcribed audio.
- **Large File Handling:** Audio files larger than 25MB are automatically split into chunks to overcome API limits.
- **Docker Deployment:** Simple deployment using Docker Compose or directly via Docker Hub.

![Screenshot of the Transcriber App](Transcriber-screenshot.png)

## Usage

1. **Upload Audio File:** Click the "File" button to select an audio file from your computer.
2. **Select API:** Choose either AssemblyAI or OpenAI Whisper from the dropdown menu.
3. **Select Language:** Choose the language of your audio or select "Automatic Detection."
4. **Transcribe:** Click the "Transcribe" button to start the transcription.
5. **View History:** Your transcriptions will appear in the "Transcription History" section, where you can copy, download, or delete them.

## Prerequisites

- **API Keys:** You must have valid API keys for AssemblyAI and/or OpenAI Whisper. Sign up at their respective websites to obtain them.
- **Docker:** Ensure that Docker is installed and running on your machine.

## Environment Variables

The application relies on the following environment variables:

| Variable                   | Description                                                                                                  | Accepted Values                                               | Default             |
| -------------------------- | ------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------- | ------------------- |
| `TZ`                       | The timezone for the application.                                                                            | Any valid timezone string (e.g., `Europe/Amsterdam`, `America/New_York`) | `UTC`               |
| `ASSEMBLYAI_API_KEY`       | Your API key for AssemblyAI.                                                                                 | Your AssemblyAI API key                                        | *None (required)*   |
| `OPENAI_API_KEY`           | Your API key for OpenAI Whisper.                                                                             | Your OpenAI API key                                           | *None (required)*   |
| `DEFAULT_TRANSCRIBE_API`   | The default transcription API used when the application loads.                                               | `assemblyai` or `openai`                                       | `assemblyai`        |
| `DEFAULT_LANGUAGE`         | The default language for transcription on startup.                                                           | `auto`, `en`, `nl`, `fr`, `es`                                 | `auto`              |

## Installation and Deployment

You have two primary options for installation and deployment:

### Option 1: Using Docker Hub (Recommended)

This is the easiest way to get started. You can pull the pre-built image from Docker Hub and run it directly.

1. **Pull the Docker Image:**

   ```bash
   docker pull arnoulddw/transcriber-app:latest
   ```

2. **Run the Docker Container:**

   ```bash
   docker run -d -p 5001:5001 \
     -e TZ="Your/Timezone" \
     -e ASSEMBLYAI_API_KEY="your_assemblyai_api_key" \
     -e OPENAI_API_KEY="your_openai_api_key" \
     -e DEFAULT_TRANSCRIBE_API="assemblyai" \
     -e DEFAULT_LANGUAGE="auto" \
     --name transcriber-app \
     arnoulddw/transcriber-app:latest
   ```

   - Replace `"Your/Timezone"` with your desired timezone (e.g., `Europe/London`).
   - Replace `"your_assemblyai_api_key"` and `"your_openai_api_key"` with your actual API keys.
   - You can change `DEFAULT_TRANSCRIBE_API` and `DEFAULT_LANGUAGE` as needed.
   - The `-d` flag runs the container in detached mode.
   - The `-p 5001:5001` flag maps port 5001 on your host machine to port 5001 in the container.
   - `--name transcriber-app` assigns a name to the container for easier management.

### Option 2: Using Docker Compose

For customization or further development, use Docker Compose to build and run the image locally.

**Step 1: Clone the Repository**

1. Open a terminal.
2. Navigate to the directory where you want to store the project.
3. Clone this repository:

   ```bash
   git clone https://github.com/arnoulddw/transcriber
   cd transcriber
   ```

**Step 2: Configure Environment Variables in docker-compose.yml**

Edit the `docker-compose.yml` file and add your API keys and other environment variables under the `environment` section. Replace the placeholder values with your actual API keys:

   ```yaml
   services:
     transcriber:
       build:
         context: .
         dockerfile: Dockerfile
       ports:
         - "5001:5001"
       volumes:
         - ./backend:/app/backend
         - ./temp_uploads:/app/temp_uploads
       environment:
         - TZ=${TZ:-UTC}
         - ASSEMBLYAI_API_KEY=your_assemblyai_api_key
         - OPENAI_API_KEY=your_openai_api_key
         - DEFAULT_TRANSCRIBE_API=${DEFAULT_TRANSCRIBE_API:-assemblyai}
         - DEFAULT_LANGUAGE=${DEFAULT_LANGUAGE:-auto}
       restart: unless-stopped
   ```

**Step 3: Build and Run with Docker Compose**

From the project’s root directory, run:

   ```bash
   docker-compose up -d --build
   ```

This builds the Docker image and starts the container in detached mode.

### Step 4: Access the Application

Open your web browser and navigate to:

   ```plaintext
   http://localhost:5001
   ```

## Development (Local Setup)

To develop or test the application locally (without Docker), follow these steps:

### Step 1: Set up a Virtual Environment

1. Open your terminal and navigate to the project directory.
2. Create a virtual environment:

   ```bash
   python3 -m venv venv
   ```

3. Activate the virtual environment:

   - On Linux/macOS:
     ```bash
     source venv/bin/activate
     ```
   - On Windows:
     ```bash
     venv\Scripts\activate
     ```

### Step 2: Install Dependencies

Install the required Python packages:

   ```bash
   pip install -r backend/requirements.txt
   ```

### Step 3: Configure Environment Variables (Local)

You can set the environment variables temporarily in your terminal:

   ```bash
   # For Linux/macOS:
   export ASSEMBLYAI_API_KEY=your_assemblyai_api_key
   export OPENAI_API_KEY=your_openai_api_key
   export TZ=Europe/London
   # Add other variables as needed
   ```

Alternatively, create a `.env` file (do not commit this file) and use python-dotenv to load the variables in your app (install with: `pip install python-dotenv`).

### Step 4: Run the Application

Start the Flask development server:

   ```bash
   python backend/app.py
   ```

Then open your browser and go to:

   ```plaintext
   http://localhost:5001
   ```

## Troubleshooting

- **Port 5001 is in use:** If this port is occupied, update the port mapping in the `docker-compose.yml` file or your Docker run command.
- **API Key Issues:** Ensure your API keys are entered correctly and that your AssemblyAI and OpenAI accounts are active.
- **File Upload Issues:** Verify that your audio file is in a supported format (mp3, mpga, m4a, wav, or webm).
- **Docker Errors:** Confirm Docker is running properly and that you have the necessary permissions.


## License

This project is licensed under the MIT License. See the LICENSE file for details.