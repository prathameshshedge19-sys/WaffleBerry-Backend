"""Presentation-only raster validation; no application imports in decoder child.

Public decode calls fail closed unless Linux libseccomp confinement is available.
The child preloads Pillow, then denies filesystem opens, networking and process
creation with a syscall allowlist. No credentials/environment or app imports are
inherited. Windows needs a separately reviewed confinement adapter; there is no
in-process production fallback. `_decode_image` is for the confined child/tests.
"""

from __future__ import annotations

import base64
import ctypes
import errno
import io
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import warnings


MAX_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 24_000_000
MAX_EDGE = 8192
MAX_RESULT_BYTES = 2 * 1024 * 1024
DECODE_TIMEOUT_SECONDS = 40
_MIMES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


class VisualReferenceError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _check_bytes(data):
    if not isinstance(data, bytes) or not data:
        raise VisualReferenceError("visual_image_empty")
    if len(data) > MAX_BYTES:
        raise VisualReferenceError("visual_image_too_large")


def _crop_values(crop):
    if hasattr(crop, "model_dump"):
        crop = crop.model_dump()
    if not isinstance(crop, dict) or set(crop) - {"x", "y", "width", "height", "rotation"}:
        raise VisualReferenceError("visual_crop_invalid")
    values = {}
    for key in ("x", "y", "width", "height"):
        value = crop.get(key)
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not 0 <= value <= 1:
            raise VisualReferenceError("visual_crop_invalid")
        values[key] = float(value)
    rotation = crop.get("rotation", 0)
    if isinstance(rotation, bool) or rotation not in (0, 90, 180, 270):
        raise VisualReferenceError("visual_crop_invalid")
    if values["width"] <= 0 or values["height"] <= 0 or values["x"] + values["width"] > 1 or values["y"] + values["height"] > 1:
        raise VisualReferenceError("visual_crop_invalid")
    return {**values, "rotation": int(rotation)}


def _decode_image(data: bytes, crop=None):
    """Internal pure decoder; caller MUST supply OS confinement for untrusted data."""
    _check_bytes(data)
    try:
        from PIL import Image, ImageFile, ImageOps
    except ImportError:
        raise VisualReferenceError("visual_decoder_unavailable") from None
    if ImageFile.LOAD_TRUNCATED_IMAGES:
        raise VisualReferenceError("visual_decoder_unsafe_configuration")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data), formats=list(_MIMES)) as probe:
                width, height = probe.size
                if not 0 < width <= MAX_EDGE or not 0 < height <= MAX_EDGE or width * height > MAX_PIXELS:
                    raise VisualReferenceError("visual_image_dimensions")
                if getattr(probe, "n_frames", 1) != 1:
                    raise VisualReferenceError("visual_image_frames")
                mime = _MIMES[probe.format]
                # Pillow can tolerate a partly missing PNG IEND CRC despite
                # verify()+load(). Require the complete container terminator.
                if probe.format == "PNG" and not data.endswith(b"\x00\x00\x00\x00IEND\xaeB`\x82"):
                    raise VisualReferenceError("visual_image_invalid")
                if probe.format == "JPEG" and not data.endswith(b"\xff\xd9"):
                    raise VisualReferenceError("visual_image_invalid")
                if probe.format == "WEBP" and int.from_bytes(data[4:8], "little") + 8 != len(data):
                    raise VisualReferenceError("visual_image_invalid")
                probe.verify()
            # verify() is not a full pixel decode. Reopen and force load, then
            # apply EXIF before measuring dimensions or interpreting any crop.
            with Image.open(io.BytesIO(data), formats=list(_MIMES)) as original:
                original.load()
                with ImageOps.exif_transpose(original) as oriented:
                    result = {"width": oriented.width, "height": oriented.height, "mime": mime}
                    if crop is None:
                        return result
                    crop = _crop_values(crop)
                    # Positive selected rotation is clockwise, matching image UI.
                    rotated = oriented.rotate(-crop["rotation"], expand=True)
                    try:
                        w, h = rotated.size
                        if crop["width"] * w < 128 or crop["height"] * h < 128:
                            raise VisualReferenceError("visual_crop_too_small")
                        # Coordinates are fractions of the oriented source axes.
                        # A rectangular source therefore needs unequal fractions
                        # for a physically square crop; never compare fractions
                        # alone or stretch the selected source rectangle.
                        if not math.isclose(crop["width"] * w, crop["height"] * h,
                                            rel_tol=0, abs_tol=1e-6):
                            raise VisualReferenceError("visual_crop_not_square")
                        box = (crop["x"] * w, crop["y"] * h,
                               (crop["x"] + crop["width"]) * w, (crop["y"] + crop["height"]) * h)
                        # Resize only the approved region; construct a fresh image
                        # so no EXIF, GPS, ICC, text or other metadata survives.
                        with rotated.convert("RGB") as rgb:
                            with rgb.resize((512, 512), Image.Resampling.LANCZOS, box=box) as resized:
                                with Image.new("RGB", (512, 512)) as clean:
                                    clean.paste(resized)
                                    output = io.BytesIO()
                                    clean.save(output, format="PNG")
                                    return output.getvalue()
                    finally:
                        rotated.close()
    except VisualReferenceError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise VisualReferenceError("visual_image_dimensions") from None
    except Exception:
        raise VisualReferenceError("visual_image_invalid") from None


def _confine_decoder():
    if sys.platform != "linux":
        raise VisualReferenceError("visual_decoder_isolation_unavailable")
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_RESULT_BYTES,) * 2)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    os.umask(0o077)
    from PIL import Image, ImageFile, ImageOps
    Image.init()  # Load plugins/codecs before filesystem access is denied.
    try:
        lib = ctypes.CDLL("libseccomp.so.2", use_errno=True)
        lib.seccomp_init.argtypes = [ctypes.c_uint32]
        lib.seccomp_init.restype = ctypes.c_void_p
        lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
        lib.seccomp_syscall_resolve_name.restype = ctypes.c_int
        lib.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
        lib.seccomp_rule_add.restype = ctypes.c_int
        lib.seccomp_load.argtypes = [ctypes.c_void_p]
        lib.seccomp_load.restype = ctypes.c_int
        lib.seccomp_release.argtypes = [ctypes.c_void_p]
        ctx = lib.seccomp_init(0x00050000 | errno.EPERM)
        if not ctx:
            raise RuntimeError()
        try:
            # No open/openat, socket/connect, exec/fork/clone, ptrace, ioctl,
            # io_uring, filesystem mutation or privilege-changing syscalls.
            allowed = "read write close fstat newfstatat lseek mmap mmap2 mprotect munmap brk mremap madvise rt_sigaction rt_sigprocmask rt_sigreturn sigaltstack futex futex_time64 clock_gettime clock_gettime64 gettimeofday getpid gettid getrandom sched_yield exit exit_group"
            for name in allowed.split():
                number = lib.seccomp_syscall_resolve_name(name.encode("ascii"))
                if number >= 0 and lib.seccomp_rule_add(ctx, 0x7FFF0000, number, 0) != 0:
                    raise RuntimeError()
            # seccomp_load enables no-new-privileges by default; failure is fatal.
            if lib.seccomp_load(ctx) != 0:
                raise RuntimeError()
        finally:
            lib.seccomp_release(ctx)
    except Exception:
        raise VisualReferenceError("visual_decoder_isolation_unavailable") from None


def _run_decoder(data: bytes, crop=None):
    _check_bytes(data)
    if crop is not None:
        crop = _crop_values(crop)
    if sys.platform != "linux":
        raise VisualReferenceError("visual_decoder_isolation_unavailable")
    command = [sys.executable, "-I", str(Path(__file__).resolve()), "--decode", json.dumps(crop, allow_nan=False)]
    # stdout is a size-limited file, not an unbounded communicate() pipe.
    # Child input stays in a pipe; the private temp root never contains originals.
    with tempfile.TemporaryDirectory(prefix="visual-decode-") as root:
        with tempfile.TemporaryFile(dir=root) as output:
            try:
                completed = subprocess.run(command, input=data, stdout=output, stderr=subprocess.DEVNULL,
                                           env={}, cwd=root, close_fds=True, timeout=DECODE_TIMEOUT_SECONDS,
                                           check=False)
            except subprocess.TimeoutExpired:
                raise VisualReferenceError("visual_decoder_timeout") from None
            except OSError:
                raise VisualReferenceError("visual_decoder_unavailable") from None
            if completed.returncode != 0:
                raise VisualReferenceError("visual_decoder_failed")
            output.seek(0)
            result_bytes = output.read(MAX_RESULT_BYTES + 1)
    try:
        if len(result_bytes) > MAX_RESULT_BYTES:
            raise ValueError()
        result = json.loads(result_bytes)
        if "error" in result:
            code = result["error"]
            if code in {"visual_decoder_isolation_unavailable", "visual_decoder_unavailable", "visual_image_empty", "visual_image_too_large", "visual_image_dimensions", "visual_image_frames", "visual_image_invalid", "visual_crop_invalid", "visual_crop_too_small", "visual_crop_not_square", "visual_decoder_unsafe_configuration"}:
                raise VisualReferenceError(code)
            raise ValueError()
        if crop is not None:
            png = base64.b64decode(result["png"], validate=True)
            if not png.startswith(b"\x89PNG\r\n\x1a\n") or len(png) > MAX_RESULT_BYTES:
                raise ValueError()
            return png
        if set(result) != {"width", "height", "mime"} or result["mime"] not in _MIMES.values():
            raise ValueError()
        if any(type(result[k]) is not int or not 0 < result[k] <= MAX_EDGE for k in ("width", "height")) or result["width"] * result["height"] > MAX_PIXELS:
            raise ValueError()
        return result
    except VisualReferenceError:
        raise
    except Exception:
        raise VisualReferenceError("visual_decoder_failed") from None


def validate_visual_reference(data: bytes, *, expected_mime_type: str | None = None) -> dict:
    result = _run_decoder(data)
    if expected_mime_type is not None and result["mime"] != expected_mime_type:
        raise VisualReferenceError("visual_image_mime_mismatch")
    return result


def normalize_crop(data: bytes, crop) -> bytes:
    """Return a metadata-free 512x512 PNG; crop follows EXIF and clockwise rotation."""
    return _run_decoder(data, crop)


if __name__ == "__main__":
    try:
        crop = json.loads(sys.argv[2])
        _confine_decoder()
        result = _decode_image(sys.stdin.buffer.read(MAX_BYTES + 1), crop)
        if isinstance(result, bytes):
            result = {"png": base64.b64encode(result).decode("ascii")}
    except VisualReferenceError as exc:
        result = {"error": exc.code}
    except ImportError:
        result = {"error": "visual_decoder_unavailable"}
    except Exception:
        result = {"error": "visual_image_invalid"}
    sys.stdout.write(json.dumps(result))
