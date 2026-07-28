import json
import logging
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    """Produces one valid JSON object per log record."""

    def format(self, record):
        payload = {
            'time': datetime.now(UTC).isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
        }
        if record.exc_info:
            payload['exception'] = self.formatException(record.exc_info)
        request = getattr(record, 'request', None)
        if request is not None:
            payload['method'] = request.method
            payload['path'] = request.path
        status_code = getattr(record, 'status_code', None)
        if status_code is not None:
            payload['status_code'] = status_code
        return json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
