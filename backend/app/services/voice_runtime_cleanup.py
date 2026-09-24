"""Private voice scratch files: process-crash-safe locks and positive cleanup.

Every voice host and the account cleanup worker must see the same dedicated
VOICE_TEMP_PATH. Never point this at a model directory or a general OS temp
directory. Locks contain no identity/content and are released by process exit.
"""
from contextlib import contextmanager, ExitStack
from contextvars import ContextVar
import os
from pathlib import Path
import shutil
import stat
import tempfile

from app.config import get_settings
from app.services.voice_providers import VoiceProviderFailure

_admission = ContextVar("private_voice_runtime_admission", default=None)
PREFIXES = ("prepare-", "synthesis-")


def _machine_id():
    return Path("/etc/machine-id").read_text(encoding="ascii").strip()


def runtime_root(settings):
    raw = Path(settings.voice_temp_path)
    root = raw.resolve()
    # Debug uses disposable local roots. Production cannot silently certify an
    # absent root on a different host, nor use per-unit /tmp namespaces.
    if not settings.legarya_debug:
        if (not raw.is_absolute() or raw != root
                or root == Path(root.anchor) or len(root.parts) < 4
                or str(root).startswith(("/tmp/", "/var/tmp/", "/run/"))
                or not root.is_dir()
                or not settings.voice_runtime_host_id
                or settings.voice_runtime_host_id != _machine_id()):
            raise OSError("voice_runtime_topology_unverified")
        for part in [*root.parents, root]:
            if part.is_symlink():
                raise OSError("voice_runtime_alias")
        info = root.stat()
        if settings.voice_runtime_root_id != f"{info.st_dev}:{info.st_ino}":
            raise OSError("voice_runtime_root_identity_mismatch")
        if info.st_mode & 0o007:
            raise OSError("voice_runtime_not_private")
    return root


@contextmanager
def runtime_admission(check):
    token = _admission.set(check)
    try:
        yield
    finally:
        _admission.reset(token)


@contextmanager
def _lock(root, prefix):
    root.mkdir(parents=True, exist_ok=True)
    target = root / ("." + prefix + "cleanup.lock")
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise OSError("voice_runtime_lock_alias")
    fd = os.open(target, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    handle = os.fdopen(fd, "r+b")
    acquired = False
    try:
        if os.name == "nt":
            import msvcrt
            if handle.seek(0, 2) == 0:
                handle.write(b"0"); handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                acquired = True
            except OSError:
                pass
        else:
            import fcntl
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except BlockingIOError:
                pass
        yield acquired
    finally:
        if acquired:
            if os.name == "nt":
                handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


@contextmanager
def private_voice_directory(prefix, *, settings=None, redirect_library_temp=False):
    if prefix not in PREFIXES:
        raise ValueError("private_runtime_scope")
    root = runtime_root(settings or get_settings())
    with _lock(root, prefix) as owned:
        if not owned:
            raise VoiceProviderFailure("voice_worker_busy")
        check = _admission.get()
        if check is not None:
            check()  # DB session closes before any scratch bytes are written.
        with tempfile.TemporaryDirectory(prefix=prefix, dir=root) as directory:
            previous = tempfile.tempdir
            try:
                # IndicF5's pinned preprocessing helper creates NamedTemporaryFile
                # without a dir parameter. The dedicated, serialized synthesis
                # process scopes those files too; no shared API-process use.
                if redirect_library_temp:
                    tempfile.tempdir = directory
                yield Path(directory)
            finally:
                if redirect_library_temp:
                    tempfile.tempdir = previous


def cleanup_voice_runtime(settings=None):
    """None means active/uncertain; int is positively removed directory count."""
    try:
        root = runtime_root(settings or get_settings())
        if not root.exists():
            return 0
        with ExitStack() as stack:
            if not all(stack.enter_context(_lock(root, prefix)) for prefix in PREFIXES):
                return None
            removed = 0
            for child in root.iterdir():
                if not child.name.startswith(PREFIXES):
                    continue
                if (child.is_symlink() or child.resolve().parent != root or not child.is_dir()
                        or getattr(child.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                    return None
                shutil.rmtree(child)
                if child.exists():
                    return None
                removed += 1
            return removed
    except OSError:
        return None
