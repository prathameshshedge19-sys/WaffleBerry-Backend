"""Real offline provider supervisor. Fake providers cannot be selected here."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import tempfile
import time

from app.services.visual_provider import (
    VisualAssetBundle, VisualBundleError, _request_digest, validate_bundle,
)
from app.services.visual_reference import normalize_crop

MODEL_SHA256 = '64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff'
MAX_OUTPUT = 3 * 1024 * 1024
WORKSPACE_MARKER = b'l19-native-workspace-v1\n'


def _workspace_root():
    root = Path(tempfile.gettempdir()) / ('l19-native-spool-' + str(os.getuid()))
    root.mkdir(mode=0o700, exist_ok=True)
    info = root.lstat()
    if root.is_symlink() or not root.is_dir() or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise VisualBundleError('visual_native_isolation_unavailable')
    return root


def sweep_native_workspaces():
    """Remove only registered private workspaces whose parent/child lock is gone."""
    import fcntl
    root = _workspace_root()
    removed = 0
    for path in root.iterdir():
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if (not path.name.startswith('l19-native-') or path.is_symlink() or not path.is_dir()
                or info.st_uid != os.getuid() or info.st_mode & 0o077):
            continue
        try:
            fd = os.open(path / '.lease', os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            continue  # Unregistered path: never guess ownership from its name.
        try:
            if os.read(fd, 64) != WORKSPACE_MARKER:
                continue
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                continue
            # Private parent, validated exact immediate child, no symlink walk.
            try:
                shutil.rmtree(path)
                removed += 1
            except FileNotFoundError:
                pass  # Another startup sweep completed the same exact path.
        finally:
            os.close(fd)
    return removed


@contextmanager
def native_workspace():
    import fcntl
    sweep_native_workspaces()
    root = Path(tempfile.mkdtemp(prefix='l19-native-', dir=_workspace_root()))
    lease = os.open(root / '.lease', os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        fcntl.flock(lease, fcntl.LOCK_EX)
        os.write(lease, WORKSPACE_MARKER)
        os.fsync(lease)
        yield str(root), lease
    finally:
        # The supervisor has already killed/reaped its native process group.
        shutil.rmtree(root)
        os.close(lease)


def _decode_output(raw, identity):
    """Treat native stdout as untrusted; never surface raw parser diagnostics."""
    if len(raw) > MAX_OUTPUT:
        raise VisualBundleError('visual_bundle_too_large')
    try:
        result = json.loads(raw)
        if type(result) is not dict:
            raise ValueError()
        if 'error' in result:
            allowed = {'visual_needs_recrop', 'visual_model_mismatch', 'visual_image_dimensions',
                       'visual_native_isolation_unavailable', 'visual_native_failed'}
            code = result['error']
            raise VisualBundleError(code if isinstance(code, str) and code in allowed else 'visual_native_failed')
        if type(result.get('assets')) is not dict:
            raise ValueError()
        bundle = VisualAssetBundle({k: base64.b64decode(v, validate=True) for k, v in result['assets'].items()})
        validate_bundle(bundle)
        rig = json.loads(bundle.assets['rig'])
        if rig['fake_only'] is not False:
            raise VisualBundleError('visual_provider_mismatch')
        if rig['request_identity_sha256'] != identity:
            raise VisualBundleError('visual_request_identity')
        return bundle, {k: result.get(k) for k in ('seconds', 'peak_rss_kib')}
    except VisualBundleError:
        raise
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise VisualBundleError('visual_native_failed') from None


class LocalPortraitRigProvider:
    provider_name = 'local-mediapipe-1.0.1'
    model_digest = MODEL_SHA256
    recipe_version = 'portrait_2d_v1'
    fake_only = False

    def __init__(self, python_executable, model_path, *, library_dir=None):
        self.python = str(Path(python_executable).resolve(strict=True))
        # Preserve venv entrypoint: resolving its symlink would lose sys.prefix.
        self.python = str(Path(python_executable).absolute())
        self.model = Path(model_path).resolve(strict=True)
        if self.model.stat().st_size != 3758596 or hashlib.sha256(self.model.read_bytes()).hexdigest() != MODEL_SHA256:
            raise VisualBundleError('visual_model_mismatch')
        self.library_dir = str(Path(library_dir).resolve(strict=True)) if library_dir else ''

    def prepare(self, data, crop, request_identity, *, cancelled=None, deadline=None):
        if sys.platform != 'linux':
            raise VisualBundleError('visual_native_isolation_unavailable')
        limit = min(deadline if deadline is not None else float('inf'), time.monotonic()+120)
        identity = _request_digest(data, request_identity)
        if cancelled and cancelled():
            raise VisualBundleError('visual_native_cancelled')
        if time.monotonic() >= limit:
            raise VisualBundleError('visual_native_timeout')
        png = normalize_crop(data, crop)
        payload = json.dumps({'png':base64.b64encode(png).decode(), 'request_digest':identity,
                              'auto_fit': crop.get('auto_fit', False)}).encode()
        # No shared spool or durable private image cache; TemporaryDirectory is
        # 0700 and cleanup happens after the entire child process group is gone.
        with native_workspace() as (root, workspace_lease):
            env = {'PATH':'/usr/bin:/bin', 'OPENBLAS_NUM_THREADS':'1', 'OMP_NUM_THREADS':'1',
                   'MKL_NUM_THREADS':'1', 'MPLCONFIGDIR':root, 'TMPDIR':root, 'HOME':root,
                   'PYTHONDONTWRITEBYTECODE':'1', 'MALLOC_ARENA_MAX':'2'}
            if self.library_dir:
                env['LD_LIBRARY_PATH'] = self.library_dir
            with tempfile.TemporaryFile(dir=root) as incoming, tempfile.TemporaryFile(dir=root) as outgoing:
                incoming.write(payload)
                incoming.seek(0)
                command = [self.python, '-I', '-B', str(Path(__file__).with_name('visual_native.py')),
                           str(self.model), self.library_dir, str(os.getpid())]
                process = subprocess.Popen(command, stdin=incoming, stdout=outgoing,
                    stderr=subprocess.DEVNULL, env=env, cwd=root, close_fds=True,
                    pass_fds=(workspace_lease,), start_new_session=True)
                try:
                    while process.poll() is None:
                        if cancelled and cancelled():
                            raise VisualBundleError('visual_native_cancelled')
                        if time.monotonic() >= limit:
                            raise VisualBundleError('visual_native_timeout')
                        time.sleep(.05)
                    if process.returncode:
                        raise VisualBundleError('visual_native_failed')
                    if cancelled and cancelled():
                        raise VisualBundleError('visual_native_cancelled')
                    if time.monotonic() >= limit:
                        raise VisualBundleError('visual_native_timeout')
                    outgoing.seek(0)
                    raw = outgoing.read(MAX_OUTPUT+1)
                    bundle, self.last_metrics = _decode_output(raw, identity)
                    return bundle
                finally:
                    # Kill descendants even if the leader crashed/exited; no
                    # orphan native process may retain portrait input handles.
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=2)


def configured_provider():
    from app.config import get_settings
    settings = get_settings()
    if not settings.visual_preparation_enabled or sys.platform != 'linux':
        raise VisualBundleError('visual_provider_unavailable')
    if not settings.visual_worker_python or not settings.visual_model_path:
        raise VisualBundleError('visual_provider_unavailable')
    return LocalPortraitRigProvider(settings.visual_worker_python, settings.visual_model_path,
                                    library_dir=settings.visual_native_library_dir)
