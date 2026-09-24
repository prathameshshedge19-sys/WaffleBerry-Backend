"""Standalone, privacy-safe SMTP alert outbox and retention/volume monitor.

Installed independently of application releases. State survives restarts on the
root disk so losing the journal volume cannot disable its own alert delivery.
No account data, DSNs, object keys, exception strings or credentials are sent.
"""
import argparse
from email.message import EmailMessage
import json
import os
from pathlib import Path
import re
import smtplib
import sqlite3
import ssl
import stat
import subprocess
import time
from uuid import UUID


def enabled(value):
    return str(value).lower() in {'true', '1', 'yes', 'on'}


def deliver(env, *, test=False):
    recipient = env.get('DELETION_ALERT_TO', '')
    sender = env.get('MAIL_FROM', '')
    if (not re.fullmatch(r'[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+', recipient)
            or not sender or '\r' in sender or '\n' in sender
            or not all(env.get(key) for key in ('MAIL_SERVER', 'MAIL_USERNAME', 'MAIL_PASSWORD'))):
        raise ValueError('alert_configuration_invalid')
    use_ssl = enabled(env.get('MAIL_SSL_TLS', 'false'))
    if not use_ssl and not enabled(env.get('MAIL_STARTTLS', 'true')):
        raise ValueError('alert_tls_required')
    message = EmailMessage()
    message['To'], message['From'] = recipient, sender
    message['Subject'] = ('LegaRya privacy monitoring - setup test' if test
                          else 'LegaRya privacy controls - operator review required')
    message.set_content(
        'This is the requested LegaRya/WaffleBerry deletion-monitoring setup test.\n'
        'No account was deleted by this test. Please confirm receipt to your operator.\n'
        if test else
        'A LegaRya/WaffleBerry deletion, retention, or independent-journal control failure was observed.\n'
        'Check the privacy-control systemd units and restricted operational health records.\n'
        'An alert may have been queued during an email outage; verify current status before acting.\n'
        'Do not restore application traffic until required deletion replay/restore gates pass.\n'
        'No customer content or credentials are included in this alert.\n')
    context = ssl.create_default_context()
    factory = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
    options = {'timeout': 10}
    if use_ssl:
        options['context'] = context
    with factory(env['MAIL_SERVER'], int(env.get('MAIL_PORT', '587')), **options) as smtp:
        if not use_ssl:
            smtp.starttls(context=context)
        smtp.login(env['MAIL_USERNAME'], env['MAIL_PASSWORD'])
        if smtp.send_message(message):
            raise RuntimeError('alert_recipient_refused')


def open_state(root):
    root = Path(root)
    if (not root.is_absolute() or root != root.resolve() or not root.is_dir()
            or (os.name != 'nt' and root.stat().st_mode & 0o077)):
        raise ValueError('alert_state_not_private')
    path = root / 'outbox.sqlite3'
    if path.is_symlink() or path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
        raise ValueError('alert_state_unsafe')
    mask = os.umask(0o077)
    try:
        db = sqlite3.connect(path, timeout=5)
    finally:
        os.umask(mask)
    db.execute('PRAGMA synchronous=FULL')
    db.execute('CREATE TABLE IF NOT EXISTS outbox (id INTEGER PRIMARY KEY CHECK(id=1), generation INTEGER NOT NULL, pending INTEGER NOT NULL, attempted REAL, accepted REAL)')
    db.execute('INSERT OR IGNORE INTO outbox VALUES(1,0,0,NULL,NULL)')
    db.commit()
    return db


def process(mode, root, *, env=None, timestamp=None, send=None):
    env = os.environ if env is None else env
    timestamp = time.time() if timestamp is None else timestamp
    send = deliver if send is None else send
    if mode == 'test':
        send(env, test=True)
        return 'test_smtp_accepted'
    db = open_state(root)
    try:
        db.execute('BEGIN IMMEDIATE')
        if mode == 'failure':
            db.execute('UPDATE outbox SET pending=1,generation=generation+1 WHERE id=1')
        generation, pending, attempted, accepted = db.execute(
            'SELECT generation,pending,attempted,accepted FROM outbox WHERE id=1').fetchone()
        if not pending:
            db.commit(); return 'idle'
        if ((attempted is not None and timestamp - attempted < 300)
                or (accepted is not None and timestamp - accepted < 900)):
            db.commit(); return 'queued'
        db.execute('UPDATE outbox SET attempted=? WHERE id=1', (timestamp,))
        db.commit()  # No state transaction spans SMTP I/O.
        send(env, test=False)
        with db:
            db.execute('UPDATE outbox SET accepted=?,pending=CASE WHEN generation=? THEN 0 ELSE pending END WHERE id=1',
                       (timestamp, generation))
        return 'smtp_accepted'
    finally:
        db.close()


def controls_healthy(env, *, timestamp=None):
    timestamp = time.time() if timestamp is None else timestamp
    try:
        root = Path(env['DELETION_JOURNAL_PATH'])
        device = (Path('/dev/disk/by-uuid') / str(UUID(env['DELETION_JOURNAL_VOLUME_UUID']))).stat()
        if not stat.S_ISBLK(device.st_mode) or not os.path.ismount(root) or root.stat().st_dev != device.st_rdev:
            return False
        catalog = Path(env.get('BACKUP_POLICY_STATE', '/var/lib/legarya-backup-policy')) / 'catalog.sqlite3'
        with sqlite3.connect(catalog.as_uri() + '?mode=ro', uri=True, timeout=5) as db:
            values = dict(db.execute('SELECT key,value FROM metadata'))
        if (values.get('policy') != 'local-backups-30d-v1'
                or not 0 <= timestamp - float(values.get('last_success', '0')) <= 7200):
            return False
        units = ['legarya-backup-expiry.timer']
        if enabled(env.get('DELETION_MONITOR_ACCOUNT_UNITS', 'false')):
            units += ['legarya-account-deletion.service', 'legarya-account-deletion-health.timer']
        # One check per unit: systemctl is-active with multiple arguments means ANY active.
        return all(subprocess.run(['systemctl', 'is-active', '--quiet', unit], timeout=5).returncode == 0 for unit in units)
    except (KeyError, OSError, ValueError, sqlite3.Error, subprocess.SubprocessError):
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=('failure', 'retry', 'monitor', 'test'))
    parser.add_argument('--state', default='/var/lib/legarya-deletion-alerts')
    args = parser.parse_args()
    try:
        mode = args.mode
        if mode == 'monitor':
            mode = 'retry' if controls_healthy(os.environ) else 'failure'
        result = process(mode, args.state)
        print(json.dumps({'event': 'privacy_alert', 'outcome': result}), flush=True)
    except Exception:
        # Keep queued obligations for the next timer/restart; never print SMTP exceptions.
        print(json.dumps({'event': 'privacy_alert', 'outcome': 'delivery_failed_retryable'}), flush=True)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
