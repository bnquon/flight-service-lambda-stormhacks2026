"""AWS Lambda entry point: lambda_function.lambda_handler."""

import base64
import json

from request import InvalidRequest, SearchRequest
from service import run_search


def response(status_code: int, payload: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload),
    }


def lambda_handler(event: dict, context: object) -> dict:
    """Accept a direct JSON invocation or an API Gateway proxy event."""
    data = event
    if isinstance(event, dict) and "body" in event:
        try:
            body = event["body"]
            if event.get("isBase64Encoded"):
                body = base64.b64decode(body, validate=True).decode("utf-8")
            data = json.loads(body)
        except (ValueError, TypeError):
            return response(400, {"error": {
                "code": "INVALID_JSON", "message": "Body must contain valid JSON.", "details": [],
            }})

    try:
        request = SearchRequest.parse(data)
    except InvalidRequest as exc:
        return response(400, {"error": {
            "code": "INVALID_REQUEST", "message": str(exc), "details": exc.details,
        }})

    # Unexpected execution errors should surface in Lambda logs, rather than
    # being mislabeled as bad input or hidden behind a placeholder response.
    result = run_search(request)
    if result is None:
        return response(503, {"error": {
            "code": "SEARCH_NOT_CONFIGURED",
            "message": "Set SKYVERN_API_KEY to enable search execution.", "details": [],
        }})
    # This slice returns final results synchronously. Use a job flow before
    # connecting a dashboard through a short-lived HTTP request.
    return response(200, result)
