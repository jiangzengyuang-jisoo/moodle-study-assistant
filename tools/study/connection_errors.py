"""Decode only allowlisted connection codes; never forward child error text."""
import json
from pathlib import Path

DESCRIPTIONS = json.loads((Path(__file__).resolve().parents[1] / 'moodle/connection-errors.json').read_text(encoding='utf-8'))


def safe_connection_error(output):
    if isinstance(output, bytes):
        output = output.decode('utf-8', errors='replace')
    for line in reversed(output.splitlines()):
        try:
            value = json.loads(line)
        except (ValueError, TypeError):
            continue
        code = value.get('connection_error') if isinstance(value, dict) else None
        if isinstance(code, str) and code in DESCRIPTIONS:
            return code, *DESCRIPTIONS[code]
    return None
