# Live Transcription Server

## Overview

The **Live Transcription Server** is a backend application built with FastAPI, designed to handle real-time audio transcription. It leverages machine learning models to transcribe audio streams received via WebSocket connections and provides transcribed text back to connected clients in real-time. This server is a crucial component of the Live Transcription system, enabling seamless and efficient audio processing.

## Features

- **Real-Time Audio Transcription**: Transcribes incoming audio streams in real-time using advanced machine learning models.
- **WebSocket Support**: Handles multiple concurrent WebSocket connections for simultaneous transcription sessions.
- **Scalable Architecture**: Utilizes worker threads and asynchronous programming to efficiently manage transcription requests.
- **Audio Storage**: Saves audio chunks to the filesystem for record-keeping and further processing.
- **Robust Error Handling**: Implements comprehensive logging and error handling to ensure reliability and ease of debugging.
- **Configurable Environment**: Automatically loads settings from environment variables (including a local `.env` file).

## Prerequisites

- [Python](https://www.python.org/) (version 3.8 or later)
- [pip](https://pip.pypa.io/en/stable/) (comes with Python)
- [Git](https://git-scm.com/) (for cloning the repository)
- [Virtual Environment](https://docs.python.org/3/library/venv.html) (recommended)

## Setup

1. **Clone the Repository**

   ```bash
   git clone https://github.com/yourusername/live-transcription-server.git
   cd live-transcription-server
   ```

2. **Create a Virtual Environment**

   It's recommended to use a virtual environment to manage dependencies.

   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows, use `venv\Scripts\activate`
   ```

3. **Install Dependencies**

   Install the required Python packages using `pip`:

   ```bash
   pip install -r requirements.txt
   ```

   The backend automatically loads a `.env` file in this directory via [`python-dotenv`](https://pypi.org/project/python-dotenv/)
   so you can override configuration without exporting variables manually.

4. **Configure Environment Variables**

   Create a `.env` file in the root directory to set up environment variables. The server reads these values at startup:

   ```env
   # Server Configuration
   PORT=8000  # Change this to use a different port
   HOST=0.0.0.0  # Use 'localhost' for local development only

   # Application Configuration
   TRANSCRIPTION_MODEL_ID=openai/whisper-medium
   AUDIO_STORAGE_DIR=audio_chunks
   LOG_LEVEL=INFO
   ```

   > **Note:** The backend defaults to the `openai/whisper-medium` checkpoint, which fits comfortably in around 8&nbsp;GB of system RAM. Override `TRANSCRIPTION_MODEL_ID` in your `.env` only if you have hardware that can support a larger model.

## Running the Server

### Development

To run the server in development mode with auto-reloading:

```bash
# Using default port (8000)
uvicorn main:app --reload

# Using custom port
uvicorn main:app --reload --port 8080

# Using environment variable
uvicorn main:app --reload --port $PORT
```

The server will be accessible at `http://localhost:<PORT>` (e.g., `http://localhost:8000` or your custom port).

### Production

For production deployment, it's recommended to use a production-ready ASGI server like **Gunicorn** with **Uvicorn** workers. Adjust the number of workers so that the Whisper checkpoint and its activations comfortably fit in memory.

#### Debian 12 (8 cores / 8&nbsp;GB RAM) deployment guide

The following checklist assumes a fresh Debian 12 server with 8 CPU cores and 8&nbsp;GB of RAM.

1. **Install system packages**

   ```bash
   sudo apt update
   sudo apt install -y python3 python3-venv python3-pip build-essential ffmpeg
   ```

   `ffmpeg` is required for audio preprocessing by the Transformers pipeline.

2. **Create application directory**

   ```bash
   sudo mkdir -p /opt/live-transcription
   sudo chown "$USER":"$USER" /opt/live-transcription
   cd /opt/live-transcription
   ```

3. **Clone the repository and set up Python**

   ```bash
   git clone https://github.com/yourusername/live-transcription-server.git .
   python3 -m venv .venv
   source .venv/bin/activate
   pip install --upgrade pip
   pip install -r requirements.txt
   pip install gunicorn
   ```

4. **Create a production `.env`**

   ```env
   HOST=0.0.0.0
   PORT=8000
   TRANSCRIPTION_MODEL_ID=openai/whisper-medium
   AUDIO_STORAGE_DIR=/opt/live-transcription/audio_chunks
   LOG_LEVEL=INFO
   ```

   With only 8&nbsp;GB of RAM, stick with the medium model and keep Gunicorn workers to 1–2 (see below) to avoid memory pressure.

5. **Prepare storage and permissions**

   ```bash
   mkdir -p /opt/live-transcription/audio_chunks
   chmod 750 /opt/live-transcription/audio_chunks
   ```

6. **Launch Gunicorn with conservative workers**

   ```bash
   source /opt/live-transcription/.venv/bin/activate
   cd /opt/live-transcription
   gunicorn main:app \
     -k uvicorn.workers.UvicornWorker \
     --bind "${HOST:-0.0.0.0}:${PORT:-8000}" \
     --workers 2 \
     --timeout 120
   ```

   Two workers balance CPU utilization against the memory footprint of the Whisper medium model. If the server starts swapping, drop to a single worker.

7. **Optional: Create a systemd service**

   ```ini
   # /etc/systemd/system/live-transcription.service
   [Unit]
   Description=Live Transcription Backend
   After=network.target

   [Service]
   Type=simple
   WorkingDirectory=/opt/live-transcription
   EnvironmentFile=/opt/live-transcription/.env
   ExecStart=/opt/live-transcription/.venv/bin/gunicorn main:app \
     -k uvicorn.workers.UvicornWorker \
     --bind 0.0.0.0:${PORT} \
     --workers=2 \
     --timeout=120
   Restart=always
   User=www-data
   Group=www-data

   [Install]
   WantedBy=multi-user.target
   ```

   Enable and start the service:

   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now live-transcription.service
   ```

8. **Optional: Place Nginx in front as a reverse proxy**

   Add Nginx if you want to terminate TLS, serve static assets, or expose the API on ports 80/443 while keeping Gunicorn bound to localhost.

   ```bash
   sudo apt install -y nginx
   sudo rm /etc/nginx/sites-enabled/default
   ```

   Create `/etc/nginx/sites-available/live-transcription` with the following contents:

   ```nginx
   upstream live_transcription_backend {
       server 127.0.0.1:8000;
   }

   server {
       listen 80;
       server_name your.domain.example;

       # Increase limits for websocket audio frames.
       client_max_body_size 50M;

       # Forward WebSocket traffic (e.g., /ws/transcribe) to the FastAPI app.
       location /ws/ {
           proxy_pass http://live_transcription_backend;
           proxy_http_version 1.1;
           proxy_set_header Upgrade $http_upgrade;
           proxy_set_header Connection "upgrade";
           proxy_set_header Host $host;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto $scheme;
       }

       # Forward any additional HTTP endpoints you expose.
       location / {
           proxy_pass http://live_transcription_backend;
           proxy_set_header Host $host;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto $scheme;
       }
   }
   ```

   With this configuration, the WebSocket client continues to connect to `ws(s)://your.domain.example/ws/transcribe`, matching the backend route documented in the [Usage](#usage) section.

   Enable the site and reload Nginx:

   ```bash
   sudo ln -s /etc/nginx/sites-available/live-transcription /etc/nginx/sites-enabled/
   sudo nginx -t
   sudo systemctl reload nginx
   ```

   For HTTPS, obtain certificates (for example with [Certbot](https://certbot.eff.org/)) and update the `server` block to listen on `443 ssl`.

These steps provide a production-ready deployment tailored to commodity Debian 12 hardware without a discrete GPU. Increase the worker count or switch to a larger Whisper model only after upgrading the memory footprint of the host.

## Project Structure

- `config.py`: Centralizes environment-driven configuration and logging defaults.
- `main.py`: Entry point for the FastAPI application.
- `real_time_audio/`: Contains modules related to real-time audio processing.
  - `__init__.py`: Initializes the transcription handler.
  - `handler.py`: Manages WebSocket connections and transcription workflows.
  - `models.py`: Defines data models used in the application.
  - `routes.py`: Defines API routes and WebSocket endpoints.
  - `service.py`: Implements the transcription service using machine learning models.
- `requirements.txt`: Lists all Python dependencies required for the project.
- `audio_chunks/`: Directory where audio chunks are stored. Created automatically if it doesn't exist.
- `README.md`: Documentation for the Live Transcription Server.

## Technologies Used

- [FastAPI](https://fastapi.tiangolo.com/): A modern, fast (high-performance) web framework for building APIs with Python.
- [Uvicorn](https://www.uvicorn.org/): A lightning-fast ASGI server for Python.
- [Gunicorn](https://gunicorn.org/): A Python WSGI HTTP Server for UNIX.
- [Transformers](https://huggingface.co/transformers/): State-of-the-art natural language processing for TensorFlow 2.0 and PyTorch.
- [Torch](https://pytorch.org/): An open-source machine learning library for Python.
- [Apollo GraphQL](https://www.apollographql.com/): A GraphQL implementation for Python.
- [Pinia](https://pinia.vuejs.org/): State management library for Vue (used in conjunction with the web frontend).
- [Vite](https://vitejs.dev/): Next-generation frontend tooling (used in conjunction with the web frontend).

## Usage

1. **Start the Server**

   Ensure the server is running by following the [Running the Server](#running-the-server) instructions.

2. **Connect the Web Client**

   The web client should be configured to connect to the server's WebSocket endpoint. Use the appropriate port in your connection URL:

   ```javascript
   // Example WebSocket connection URL
   const wsUrl = `ws://localhost:${PORT}/ws/transcribe`
   ```

   Configure this in your web application's settings (e.g., `nuxt.config.ts`).

3. **Begin Transcription**

   Use the web application's interface to start sending audio streams. The server will process the audio in real-time and send back transcribed text.

## Logging

The server uses Python's built-in `logging` module to log important events and errors. Logs are printed to the console with the default log level set to `INFO`. You can adjust the log level by setting the `LOG_LEVEL` environment variable in the `.env` file.

## Contributing

Contributions are welcome! Please follow these steps to contribute:

1. **Fork the Repository**

2. **Create a Feature Branch**

   ```bash
   git checkout -b feature/YourFeature
   ```

3. **Commit Your Changes**

   ```bash
   git commit -m "Add your message"
   ```

4. **Push to the Branch**

   ```bash
   git push origin feature/YourFeature
   ```

5. **Open a Pull Request**

   Describe your changes and submit the pull request for review.

## License

This project is licensed under the [MIT License](LICENSE).

## Contact

For any questions, suggestions, or support, please contact [your.email@example.com](mailto:your.email@example.com).

## Acknowledgements

- [OpenAI Whisper](https://github.com/openai/whisper) for the powerful transcription models.
- [FastAPI](https://fastapi.tiangolo.com/) for the excellent web framework.
- [Hugging Face Transformers](https://huggingface.co/transformers/) for providing state-of-the-art machine learning models.
- [Uvicorn](https://www.uvicorn.org/) and [Gunicorn](https://gunicorn.org/) for the ASGI and WSGI server implementations.

## Future Improvements

- **Dockerization**: Containerize the server for easier deployment and scalability.
- **Authentication**: Implement authentication mechanisms to secure WebSocket connections.
- **Enhanced Error Handling**: Improve error handling to cover more edge cases and provide better feedback.
- **Performance Optimization**: Optimize the transcription pipeline for lower latency and higher throughput.
- **Monitoring and Metrics**: Integrate monitoring tools to track server performance and usage metrics.

## References

- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [Transformers Documentation](https://huggingface.co/transformers/)
- [Uvicorn Documentation](https://www.uvicorn.org/)
- [Gunicorn Documentation](https://docs.gunicorn.org/en/stable/)