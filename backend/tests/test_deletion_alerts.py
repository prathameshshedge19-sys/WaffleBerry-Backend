from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4
import ssl
import stat

import pytest

from app.services import deletion_alerts as alerts
from app.services.backup_retention import PolicyError
from app.services.deletion_journal import configured, verify_volume


def test_failed_delivery_survives_restart_and_eventually_retries(tmp_path):
    tmp_path.chmod(0o700)
    def failed(*args, **kwargs):
        raise OSError('synthetic failure')
    with pytest.raises(OSError):
        alerts.process('failure', tmp_path, timestamp=100, send=failed)
    sent = []
    send = lambda *args, **kwargs: sent.append(kwargs)
    assert alerts.process('retry', tmp_path, timestamp=399, send=send) == 'queued'
    assert alerts.process('retry', tmp_path, timestamp=400, send=send) == 'smtp_accepted'
    assert alerts.process('retry', tmp_path, timestamp=401, send=send) == 'idle'
    assert sent == [{'test': False}]


def test_new_event_during_delivery_is_not_lost_and_is_rate_limited(tmp_path):
    tmp_path.chmod(0o700)
    def concurrent(env, **kwargs):
        assert alerts.process('failure', tmp_path, timestamp=101, send=lambda *a, **k: None) == 'queued'
    assert alerts.process('failure', tmp_path, timestamp=100, send=concurrent) == 'smtp_accepted'
    assert alerts.process('retry', tmp_path, timestamp=999, send=lambda *a, **k: None) == 'queued'
    assert alerts.process('retry', tmp_path, timestamp=1000, send=lambda *a, **k: None) == 'smtp_accepted'


def test_test_email_does_not_acknowledge_or_create_real_failure(tmp_path):
    seen = []
    assert alerts.process('test', tmp_path, send=lambda *a, **kw: seen.append(kw)) == 'test_smtp_accepted'
    assert seen == [{'test': True}] and not (tmp_path / 'outbox.sqlite3').exists()


def test_smtp_uses_verified_tls_and_no_private_payload(monkeypatch):
    sent, stages = [], []
    class SMTP:
        def __init__(self, *args, **kwargs):
            assert kwargs['timeout'] == 10
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def starttls(self, *, context):
            assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
            stages.append('tls')
        def login(self, *args): stages.append('login')
        def send_message(self, message):
            stages.append('send'); sent.append(message); return {}
    monkeypatch.setattr(alerts.smtplib, 'SMTP', SMTP)
    env = {'DELETION_ALERT_TO': 'operator@example.invalid', 'MAIL_FROM': 'app@example.invalid',
           'MAIL_SERVER': 'smtp.example.invalid', 'MAIL_USERNAME': 'synthetic-user',
           'MAIL_PASSWORD': 'synthetic-password', 'DATABASE_URL': 'never-send-private-dsn'}
    alerts.deliver(env)
    assert stages == ['tls', 'login', 'send']
    raw = sent[0].as_string()
    for value in ('synthetic-password', 'synthetic-user', 'never-send-private-dsn'):
        assert value not in raw
    with pytest.raises(ValueError, match='tls'):
        alerts.deliver(dict(env, MAIL_STARTTLS='false'))


def test_production_journal_requires_pinned_volume():
    settings = SimpleNamespace(legarya_debug=False, deletion_journal_path='/synthetic',
                               deletion_lineage=str(uuid4()), deletion_journal_volume_uuid=None)
    with pytest.raises(PolicyError, match='volume_required'):
        configured(settings)


@pytest.mark.parametrize('mounted,device_id,block,okay', [(True, 8, True, True),
    (False, 8, True, False), (True, 9, True, False), (True, 8, False, False)])
def test_journal_never_falls_back_to_root_or_wrong_volume(mounted, device_id, block, okay):
    root = SimpleNamespace(stat=lambda: SimpleNamespace(st_dev=8))
    device = SimpleNamespace(st_rdev=device_id, st_mode=stat.S_IFBLK if block else stat.S_IFREG)
    with patch.object(Path, 'stat', return_value=device), patch('os.path.ismount', return_value=mounted):
        if okay:
            verify_volume(root, str(uuid4()))
        else:
            with pytest.raises(PolicyError, match='volume_mismatch'):
                verify_volume(root, str(uuid4()))


def test_invalid_volume_id_fails_without_path_traversal():
    with pytest.raises(PolicyError, match='volume_unavailable'):
        verify_volume(Path('/synthetic'), '../../different-device')


def test_monitor_checks_each_required_unit_and_stale_backup_health(tmp_path, monkeypatch):
    import sqlite3
    root = tmp_path / 'journal'; root.mkdir()
    catalog = tmp_path / 'catalog.sqlite3'
    with sqlite3.connect(catalog) as db:
        db.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT)')
        db.executemany('INSERT INTO metadata VALUES(?,?)', [('policy', 'local-backups-30d-v1'), ('last_success', '100')])
    env = {'DELETION_JOURNAL_PATH': str(root), 'DELETION_JOURNAL_VOLUME_UUID': str(uuid4()),
           'BACKUP_POLICY_STATE': str(tmp_path), 'DELETION_MONITOR_ACCOUNT_UNITS': 'true'}
    device = SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=root.stat().st_dev)
    original_stat = Path.stat
    calls = []
    def checked_run(command, **kwargs):
        calls.append(command[-1])
        return SimpleNamespace(returncode=1 if command[-1] == 'legarya-account-deletion.service' else 0)
    with patch.object(Path, 'stat', lambda path, **kwargs: device if path.parent.name == 'by-uuid' else original_stat(path, **kwargs)), \
            patch('os.path.ismount', return_value=True):
        monkeypatch.setattr(alerts.subprocess, 'run', checked_run)
        assert not alerts.controls_healthy(env, timestamp=101)
        assert calls == ['legarya-backup-expiry.timer', 'legarya-account-deletion.service']
        calls.clear()
        assert not alerts.controls_healthy(env, timestamp=7301)
        assert not calls
