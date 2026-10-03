"""Status messages; the WebSocket destination is still undecided."""

from datetime import datetime, timezone
import json


def publish_update(session_id: str, search_id: str, message_type: str, **payload) -> None:
    # TODO: send this envelope to the chosen WebSocket backend. For now, log only.
    print(json.dumps({
        "version": 1,
        "type": message_type,
        "session_id": session_id,
        "search_id": search_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **payload,
    }), flush=True)
