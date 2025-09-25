import logging

from fastapi import FastAPI

from config import settings  # noqa: F401 ensures env config loads before routes
from real_time_audio.routes import router as audio_router

app = FastAPI()

app.include_router(audio_router)


@app.on_event("startup")
async def log_startup_configuration() -> None:
    logging.getLogger(__name__).info(
        "Live Transcription Server starting with model '%s' and audio dir '%s'",
        settings.transcription_model_id,
        settings.audio_storage_dir,
    )

# To run the server, use the command:
# uvicorn main:app --reload