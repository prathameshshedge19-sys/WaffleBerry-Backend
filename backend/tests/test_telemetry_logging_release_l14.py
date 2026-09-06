"""Exercise production logging defaults in fresh processes, outside pytest capture."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize('configured', [False, True])
def test_uvicorn_emits_one_safe_summary_without_debug_spam(configured):
    code = '''
import logging, logging.config, json
from uvicorn.config import LOGGING_CONFIG
logging.config.dictConfig(LOGGING_CONFIG)
if CONFIGURED:
    logging.basicConfig(level=logging.INFO, format='%(message)s')
before = tuple(logging.root.handlers)
from app.services import turn_observability as obs
assert tuple(logging.root.handlers) == before
obs.emit('stage_finished', values={'provider_generation': 1})
obs.emit('turn_completed', level=logging.INFO,
         dimensions={'mode': 'rya', 'raw_query': 'PRIVATE_RELEASE_SENTINEL'},
         values={'delta_count': 30, 'raw_prompt': 'PRIVATE_RELEASE_SENTINEL'})
obs.sink.queue.join()
'''.replace('CONFIGURED', str(configured))
    result = subprocess.run([sys.executable, '-B', '-c', code],
                            cwd=Path(__file__).resolve().parents[1],
                            env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    lines = result.stderr.splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event['event'] == 'turn_completed' and event['delta_count'] == 30
    assert 'PRIVATE_RELEASE_SENTINEL' not in result.stderr
    assert result.stdout == ''
