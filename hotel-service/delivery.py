"""Wait for one browser recording, archive it in S3, and deliver final results."""

import asyncio
import json
import logging
import os
from pathlib import Path
import tempfile
from urllib.parse import quote
from urllib.request import Request, urlopen

from search_logging import log_event, log_step


def configured(name: str) -> str:
    value = os.getenv(name, "").strip()
    return "" if value == "replace_me" else value


async def archive_recording(skyvern, record: dict, sources: list[dict], prefix: str) -> None:
    record.update(recording_url=None, recording_error=None, delivery_error=None)
    bucket = configured("RECORDINGS_S3_BUCKET")
    if not bucket:
        log_event("recording.archive", "skipped", search_id=record["search_id"], reason="RECORDINGS_S3_BUCKET not configured")
        return
    try:
        # A single deadline covers every origin; only the first available video is uploaded.
        wait_seconds = float(os.getenv("RECORDING_WAIT_SECONDS", "90"))
        with log_step("recording.wait", search_id=record["search_id"], timeout_seconds=wait_seconds):
            async with asyncio.timeout(wait_seconds):
                recording = await wait_for_recording(skyvern, sources)
        with log_step("recording.upload", search_id=record["search_id"]):
            record["recording_url"] = await asyncio.to_thread(
                upload_recording, recording, bucket, prefix, record["search_id"],
            )
    except Exception:
        # Recording processing/upload failures must not discard successful search results.
        logging.exception("Recording processing or S3 upload failed")
        record["recording_error"] = {
            "code": "RECORDING_UPLOAD_FAILED",
            "message": "Recording was not ready or could not be uploaded; see worker logs.",
        }


async def wait_for_recording(skyvern, sources: list[dict]) -> dict:
    sources = [source for source in sources if source.get("skyvern_browser_session_id")]
    if not sources:
        raise ValueError("No browser session was created.")
    attempt = 0
    while True:
        attempt += 1
        log_event("recording.poll", "started", attempt=attempt, browser_count=len(sources))
        for source in sources:
            if source.get("recordings"):
                return source["recordings"][0]
            session = await asyncio.wait_for(
                skyvern.get_browser_session(source["skyvern_browser_session_id"]), timeout=10,
            )
            source["recordings"] = [
                {"url": recording.url, "filename": recording.filename}
                for recording in session.recordings or []
            ]
            if source["recordings"]:
                source["replay_url"] = source["recordings"][0]["url"]
                return source["recordings"][0]
        await asyncio.sleep(3)


def upload_recording(recording: dict, bucket: str, prefix: str, search_id: str) -> str:
    # Boto3 is included in the AWS Lambda Python runtime; credentials come from its role.
    import boto3
    from boto3.s3.transfer import TransferConfig
    from botocore.config import Config

    region = configured("RECORDINGS_S3_REGION") or "us-west-2"
    extension = Path(recording.get("filename") or "recording.webm").suffix.lower()
    extension = extension if extension in (".mp4", ".webm") else ".webm"
    key = f"{prefix}/{search_id}{extension}"
    content_type = "video/mp4" if extension == ".mp4" else "video/webm"
    # Stream to temporary disk instead of holding the full video in Lambda memory.
    with tempfile.TemporaryFile() as video:
        with log_step("recording.download", search_id=search_id):
            with urlopen(recording["url"], timeout=30) as response:
                while chunk := response.read(1024 * 1024):
                    video.write(chunk)
            log_event("recording.download_size", "completed", bytes=video.tell())
        video.seek(0)
        client = boto3.client("s3", region_name=region, config=Config(
            connect_timeout=10, read_timeout=30, retries={"total_max_attempts": 1},
        ))
        with log_step("recording.s3_upload", search_id=search_id):
            client.upload_fileobj(video, bucket, key,
                                  ExtraArgs={"ContentType": content_type},
                                  Config=TransferConfig(use_threads=False))
    return f"https://{bucket}.s3.{region}.amazonaws.com/{quote(key, safe='/')}"


def post_results(record: dict, endpoint_variable: str, callback_url: str | None = None) -> bool:
    """POST once when configured; return false if delivery failed."""
    endpoint = callback_url or configured(endpoint_variable)
    if not endpoint:
        log_event("results.post", "skipped", session_id=record["session_id"],
                  search_id=record["search_id"], reason="Result endpoint not configured")
        return True
    try:
        with log_step("results.post", session_id=record["session_id"], search_id=record["search_id"],
                      destination="callback" if callback_url else "environment"):
            request = Request(endpoint, data=json.dumps(record).encode(),
                              headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(request, timeout=15) as response:
                log_event("results.post_response", "received", http_status=response.status)
                if not 200 <= response.status < 300:
                    raise ValueError("Results endpoint returned an unsuccessful status.")
        return True
    except Exception:
        # The receiver may have accepted a request before a timeout; do not retry blindly.
        logging.exception("Results POST failed")
        record["delivery_error"] = {
            "code": "RESULTS_POST_FAILED",
            "message": "Could not confirm result delivery; see worker logs.",
        }
        return False
