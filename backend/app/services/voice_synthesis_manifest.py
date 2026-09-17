"""Immutable, fail-closed artifact identity for the L21.4 synthesis worker."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path


INDICF5_REPO = "ai4bharat/IndicF5"
INDICF5_REVISION = "ba85abedf18dc479a447eaa0eccbd76ab78a47d5"
INDICF5_SOURCE_REPO = "https://github.com/AI4Bharat/IndicF5.git"
INDICF5_SOURCE_REVISION = "13f7c4d627cc10111aea8fe9c0039462cacacdc7"
VOCOS_REPO = "charactr/vocos-mel-24khz"
VOCOS_REVISION = "0feb3fdd929bcd6649e0e7c5a688cf7dd012ef21"
INFERENCE_CONFIG = {
    "nfe_step": 48,
    "cfg_strength": 1.65,
    "sway_sampling_coef": -1.0,
    "speed": 0.97,
    "cross_fade_duration": 0.10,
    "target_rms": 0.1,
    "sample_rate": 24000,
    "channels": 1,
}


class ManifestError(RuntimeError):
    pass


def canonical_digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False).encode("utf-8")).hexdigest()


def inference_config_digest() -> str:
    return canonical_digest(INFERENCE_CONFIG)


@dataclass(frozen=True)
class VerifiedManifest:
    value: dict
    digest: str
    root: Path


def _hash_file(path: Path) -> tuple[int, str]:
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def load_and_verify_manifest(path: str | Path, artifact_root: str | Path) -> VerifiedManifest:
    """Validate identity and every local artifact before importing model code.

    There is intentionally no network fallback. Missing hashes/files fail
    startup, including the gated files that could not be resolved in L21.4.
    """
    manifest_path, root = Path(path).resolve(), Path(artifact_root).resolve()
    try:
        raw = manifest_path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestError("voice_manifest_unavailable") from exc
    if (value.get("schema") != "l21-indicf5-manifest-v1"
            or value.get("model", {}).get("repo") != INDICF5_REPO
            or value.get("model", {}).get("revision") != INDICF5_REVISION
            or value.get("source", {}).get("repo") != INDICF5_SOURCE_REPO
            or value.get("source", {}).get("revision") != INDICF5_SOURCE_REVISION
            or value.get("vocoder", {}).get("repo") != VOCOS_REPO
            or value.get("vocoder", {}).get("revision") != VOCOS_REVISION
            or value.get("inference") != INFERENCE_CONFIG):
        raise ManifestError("voice_manifest_identity_invalid")
    files = value.get("files")
    if not isinstance(files, list) or not files:
        raise ManifestError("voice_manifest_files_invalid")
    seen: set[str] = set()
    for item in files:
        relative, expected, size = item.get("path"), item.get("sha256"), item.get("bytes")
        if (not isinstance(relative, str) or relative in seen
                or not isinstance(expected, str) or len(expected) != 64
                or type(size) is not int or size <= 0):
            raise ManifestError("voice_manifest_files_invalid")
        seen.add(relative)
        candidate = (root / relative).resolve()
        if root != candidate and root not in candidate.parents:
            raise ManifestError("voice_manifest_path_invalid")
        try:
            actual_size, actual_digest = _hash_file(candidate)
        except OSError as exc:
            raise ManifestError("voice_model_artifact_unavailable") from exc
        if actual_size != size or actual_digest != expected:
            raise ManifestError("voice_model_artifact_mismatch")
    required = {"indicf5/model.py", "indicf5/model.safetensors",
        "indicf5/config.json", "indicf5/checkpoints/vocab.txt",
        "vocos/config.yaml", "vocos/pytorch_model.bin"}
    if not required.issubset(seen):
        raise ManifestError("voice_manifest_files_incomplete")
    return VerifiedManifest(value=value, digest=canonical_digest(value), root=root)


def test_manifest() -> VerifiedManifest:
    value = {
        "schema": "l21-indicf5-manifest-v1", "model": {"repo": INDICF5_REPO,
        "revision": INDICF5_REVISION}, "source": {"repo": INDICF5_SOURCE_REPO,
        "revision": INDICF5_SOURCE_REVISION}, "vocoder": {"repo": VOCOS_REPO,
        "revision": VOCOS_REVISION}, "inference": dict(INFERENCE_CONFIG),
        "files": [], "test_only": True,
    }
    return VerifiedManifest(value=value, digest=canonical_digest(value), root=Path("."))
