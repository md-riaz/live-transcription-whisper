import asyncio
import logging
import queue
import threading
import time
import uuid
from datetime import datetime
from typing import Dict, NamedTuple, Optional

from fastapi import WebSocket

from .service import TranscriptionService
from config import settings

logger = logging.getLogger(__name__)

# Audio parameters - must match frontend MediaRecorder settings
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit audio
SAMPLE_RATE = 16000

# Audio storage configuration
AUDIO_STORAGE_DIR = settings.audio_storage_dir


class TranscriptionRequest(NamedTuple):
    """Structure for holding transcription request data"""
    session_id: str
    audio_data: bytes
    timestamp: float
    audio_file_path: str


class TranscriptionWorker(threading.Thread):
    """Single worker thread that processes all transcription requests sequentially"""

    def __init__(self, transcription_service: TranscriptionService):
        super().__init__()
        self.transcription_service = transcription_service
        self.request_queue = queue.Queue(maxsize=100)
        self.result_queues: Dict[str, asyncio.Queue] = {}
        self.running = True
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Update the event loop used for delivering transcription results."""
        self.loop = loop

    def run(self):
        """Process transcription requests sequentially"""
        while self.running:
            try:
                # Get request with timeout to allow checking running flag
                try:
                    request = self.request_queue.get(timeout=0.5)
                except queue.Empty:
                    continue

                if request is None:  # Shutdown signal
                    break

                try:
                    # Process transcription
                    transcription = self.transcription_service.transcribe(request.audio_data)
                    
                    # If session still exists, send result
                    if request.session_id in self.result_queues:
                        result_queue = self.result_queues[request.session_id]
                        if self.loop and not self.loop.is_closed():
                            asyncio.run_coroutine_threadsafe(
                                result_queue.put({
                                    "type": "transcription",
                                    "text": transcription,
                                    "timestamp": request.timestamp,
                                    "audio_file": request.audio_file_path
                                }),
                                self.loop,
                            )
                        else:
                            logger.warning(
                                "No active event loop available for session %s; dropping transcription result.",
                                request.session_id,
                            )
                except Exception as e:
                    logger.error(f"Error processing transcription: {e}")

            except Exception as e:
                logger.error(f"Error in transcription worker: {e}")

    def stop(self):
        """Signal the thread to stop"""
        self.running = False
        self.request_queue.put(None)  # Unblock the queue if waiting


class AudioSaveWorker(threading.Thread):
    """Worker thread for saving audio chunks to disk"""
    def __init__(self, save_queue: queue.Queue):
        super().__init__()
        self.save_queue = save_queue
        self.running = True

    def run(self):
        """Process audio save requests"""
        while self.running:
            try:
                # Get save request with timeout to allow checking running flag
                try:
                    session_id, audio_data, timestamp, response_queue = self.save_queue.get(timeout=0.5)
                except queue.Empty:
                    continue

                try:
                    # Save the audio chunk
                    file_path = save_audio_chunk(session_id, audio_data, timestamp)
                    logger.info(f"Saved WAV audio chunk: {file_path}")
                    # Send the file path back to the requester
                    response_queue.put(file_path)
                except Exception as e:
                    logger.error(f"Error saving audio chunk: {e}")
                    response_queue.put(None)

            except Exception as e:
                logger.error(f"Error in audio save worker: {e}")

    def stop(self):
        """Signal the thread to stop"""
        self.running = False


def save_audio_chunk(session_id: str, audio_data: bytes, timestamp: float) -> str:
    """Save WAV audio chunk to file and return the file path"""
    timestamp_str = datetime.fromtimestamp(timestamp).strftime('%Y%m%d_%H%M%S_%f')
    filename = f"{session_id}_{timestamp_str}.wav"
    file_path = AUDIO_STORAGE_DIR / filename
    
    try:
        with open(file_path, 'wb') as f:
            f.write(audio_data)
        return str(file_path)
    except Exception as e:
        logger.error(f"Error saving audio chunk: {e}")
        raise


class TranscriptionHandler:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(TranscriptionHandler, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, '_initialized') and self._initialized:
            return
        self.transcription_service = TranscriptionService()
        self.active_connections: Dict[str, WebSocket] = {}
        self.output_queues: Dict[str, asyncio.Queue] = {}
        
        self.loop: Optional[asyncio.AbstractEventLoop] = None

        self.worker = TranscriptionWorker(self.transcription_service)
        self.worker.start()

        # Initialize audio save worker
        self.audio_save_queue = queue.Queue(maxsize=100)
        self.audio_save_worker = AudioSaveWorker(self.audio_save_queue)
        self.audio_save_worker.start()
        
        self._initialized = True

    async def connect(self, websocket: WebSocket) -> str:
        """Initialize WebSocket connection"""
        await websocket.accept()

        session_id = str(uuid.uuid4())
        self.active_connections[session_id] = websocket

        loop = asyncio.get_running_loop()
        self.loop = loop
        self.worker.set_loop(loop)

        # Initialize output queue and add to worker
        self.output_queues[session_id] = asyncio.Queue(maxsize=100)
        self.worker.result_queues[session_id] = self.output_queues[session_id]

        # Start async tasks for WebSocket communication
        asyncio.create_task(self._receive_audio(websocket, session_id))
        asyncio.create_task(self._send_results(websocket, session_id))

        await websocket.send_json({
            "type": "session_init",
            "session_id": session_id
        })

        logger.info(f"Session {session_id} connected.")
        return session_id

    async def _receive_audio(self, websocket: WebSocket, session_id: str):
        """Receive audio chunks from websocket and process them"""
        try:
            while True:
                # Receive WAV audio data directly from frontend
                audio_data = await websocket.receive_bytes()
                current_timestamp = time.time()
                
                try:
                    # Initialize a response queue to receive the file path
                    response_queue = queue.Queue(maxsize=1)
                    # Enqueue the save request
                    self.audio_save_queue.put_nowait((session_id, audio_data, current_timestamp, response_queue))
                    
                    # Wait for the file path to be saved without blocking the event loop
                    audio_file_path = await asyncio.to_thread(response_queue.get)
                    
                    if audio_file_path is None:
                        raise Exception("Failed to save audio chunk.")
                    
                    # Create transcription request with WAV data directly
                    request = TranscriptionRequest(
                        session_id=session_id,
                        audio_data=audio_data,
                        timestamp=current_timestamp,
                        audio_file_path=audio_file_path
                    )
                    
                    try:
                        self.worker.request_queue.put_nowait(request)
                    except queue.Full:
                        logger.warning(f"Request queue full for {session_id}, dropping chunk.")
                        await websocket.send_json({
                            "type": "warning",
                            "message": "Audio chunk dropped - processing backlog."
                        })
                        
                except Exception as e:
                    logger.error(f"Error processing audio chunk: {e}")
                    await websocket.send_json({
                        "type": "warning",
                        "message": "Error processing audio chunk."
                    })
                        
        except Exception as e:
            logger.error(f"Error receiving audio for session {session_id}: {e}")
            await self.disconnect(session_id)

    async def _send_results(self, websocket: WebSocket, session_id: str):
        """Send transcription results back through WebSocket"""
        try:
            while True:
                result = await self.output_queues[session_id].get()
                await websocket.send_json(result)
        except Exception as e:
            logger.error(f"Error sending results for session {session_id}: {e}")
            await self.disconnect(session_id)

    async def disconnect(self, session_id: str):
        """Clean up all resources for a session"""
        try:
            # Remove session's output queue from worker
            if session_id in self.worker.result_queues:
                del self.worker.result_queues[session_id]

            # Clean up queues
            if session_id in self.output_queues:
                del self.output_queues[session_id]

            # Close WebSocket
            if session_id in self.active_connections:
                await self.active_connections[session_id].close()
                del self.active_connections[session_id]

        except Exception as e:
            logger.error(f"Error during disconnect cleanup for session {session_id}: {e}")
        finally:
            logger.info(f"Session {session_id} disconnected and cleaned up.")

    async def shutdown(self):
        """Shutdown the handler and worker threads"""
        try:
            self.worker.stop()
            self.worker.join(timeout=5.0)
            if self.worker.is_alive():
                logger.warning("Transcription worker thread did not stop gracefully")
        except Exception as e:
            logger.error(f"Error during transcription worker shutdown: {e}")
        
        try:
            self.audio_save_worker.stop()
            self.audio_save_worker.join(timeout=5.0)
            if self.audio_save_worker.is_alive():
                logger.warning("Audio save worker thread did not stop gracefully")
        except Exception as e:
            logger.error(f"Error during audio save worker shutdown: {e}")

transcription_handler = TranscriptionHandler()
