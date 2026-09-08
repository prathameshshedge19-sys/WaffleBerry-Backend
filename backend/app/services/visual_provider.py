"""L19 data-only preparation contract and bounded portrait_2d_v1 validation.

Call preparation AND validation outside SQL transactions in the visual worker.
The only implementation here is an explicitly fake, non-subject square grid:
it detects no faces and makes no likeness, blinking or facial-quality claim.
Real adapters require separate package/model licensing and compatibility gates.

Recipe coordinates/UVs are normalized, x right/y down. Triangles have positive
signed area. Each channel is an additive vertical displacement in [0, 1]; the
application owns the formula y += mouth * mouth_dy + blink * blink_dy. No
downloaded expressions, shaders, URLs, arbitrary attributes or image metadata.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import itertools
import json
import math
import struct
from typing import Protocol
import zlib

from app.services.visual_reference import normalize_crop


RECIPE_VERSION = "portrait_2d_v1"
MAX_BUNDLE_BYTES = 2 * 1024 * 1024
MAX_RIG_BYTES = 128 * 1024
MAX_POSTER_BYTES = 100 * 1024
MAX_VERTICES = 512
MAX_TRIANGLES = 1024
ASSET_ROLES = ("poster", "texture_atlas", "rig")
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_CHANNELS = ("mouth", "blink_left", "blink_right")
_AMPLITUDES = {"mouth": 0.015, "blink_left": 0.02, "blink_right": 0.02}


class VisualBundleError(ValueError):
    """Terminal, sanitized provider-output failure; safe to map to a job code."""

    def __init__(self, code: str = "visual_bundle_invalid"):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class VisualAssetBundle:
    assets: dict[str, bytes]


class VisualPreparationProvider(Protocol):
    provider_name: str
    model_digest: str
    recipe_version: str

    def prepare(self, data: bytes, crop: dict, request_identity: dict) -> VisualAssetBundle: ...


def _require(condition, code="visual_bundle_invalid"):
    if not condition:
        raise VisualBundleError(code)


def _keys(value, keys):
    _require(type(value) is dict and set(value) == set(keys), "visual_rig_schema")


def _number(value, lower, upper):
    _require(type(value) in (int, float) and lower <= value <= upper
             and math.isfinite(value), "visual_rig_number")
    return value


def _digest(value):
    _require(type(value) is str and len(value) == 64
             and all(c in "0123456789abcdef" for c in value), "visual_rig_digest")


def _json_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "visual_rig_schema")
        result[key] = value
    return result


def _reject_constant(_value):
    raise VisualBundleError("visual_rig_number")


def _png(data: bytes, *, poster=False):
    """Validate an exact bounded RGB/RGBA PNG stream before full pixel decode.

    Only IHDR/IDAT/IEND are permitted: no EXIF, text, ICC, APNG, unknown chunks,
    appended content or unused compressed payload. Encoders emit fresh pixels.
    """
    from PIL import Image, ImageFile

    _require(not ImageFile.LOAD_TRUNCATED_IMAGES, "visual_decoder_unsafe_configuration")
    _require(data.startswith(_PNG_SIGNATURE), "visual_image_invalid")
    offset, chunks, compressed = 8, [], bytearray()
    width = height = channels = 0
    while offset < len(data):
        _require(len(chunks) < 128 and offset + 12 <= len(data), "visual_image_invalid")
        size = struct.unpack_from(">I", data, offset)[0]
        kind = data[offset + 4:offset + 8]
        end = offset + 12 + size
        _require(end <= len(data), "visual_image_invalid")
        payload = data[offset + 8:end - 4]
        crc = struct.unpack_from(">I", data, end - 4)[0]
        _require(zlib.crc32(kind + payload) & 0xffffffff == crc, "visual_image_invalid")
        if not chunks:
            _require(kind == b"IHDR" and size == 13, "visual_image_invalid")
            width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", payload)
            _require(1 <= width <= 1024 and 1 <= height <= 1024, "visual_image_dimensions")
            _require(not poster or (width, height) == (256, 256), "visual_image_dimensions")
            _require(depth == 8 and color in (2, 6) and (compression, filtering, interlace) == (0, 0, 0),
                     "visual_image_invalid")
            channels = 3 if color == 2 else 4
        elif kind == b"IDAT":
            _require(chunks[-1] in (b"IHDR", b"IDAT") and size > 0, "visual_image_invalid")
            compressed.extend(payload)
        elif kind == b"IEND":
            _require(chunks[-1] == b"IDAT" and size == 0 and end == len(data), "visual_image_invalid")
        else:
            raise VisualBundleError("visual_image_metadata")
        chunks.append(kind)
        offset = end
    _require(chunks and chunks[-1] == b"IEND", "visual_image_invalid")
    expected = height * (1 + width * channels)
    try:
        decoder = zlib.decompressobj()
        pixels = decoder.decompress(compressed, expected + 1)
        _require(len(pixels) == expected and decoder.eof and not decoder.unused_data
                 and not decoder.unconsumed_tail, "visual_image_invalid")
        _require(all(pixels[i] <= 4 for i in range(0, expected, 1 + width * channels)), "visual_image_invalid")
        with Image.open(io.BytesIO(data), formats=["PNG"]) as decoded:
            decoded.load()
            _require(not decoded.info and getattr(decoded, "n_frames", 1) == 1, "visual_image_metadata")
            return decoded.copy()
    except VisualBundleError:
        raise
    except (OSError, ValueError, zlib.error):
        raise VisualBundleError("visual_image_invalid") from None


def _encode_png(image):
    from PIL import Image

    with Image.new("RGB", image.size) as clean:
        clean.paste(image)
        output = io.BytesIO()
        clean.save(output, format="PNG", optimize=False)
        return output.getvalue()


def _area(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _validate_rig(raw, atlas, poster):
    try:
        rig = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs, parse_constant=_reject_constant)
    except (UnicodeError, ValueError, RecursionError):
        raise VisualBundleError("visual_rig_schema") from None
    _keys(rig, ("recipe_version", "topology_version", "fake_only", "request_identity_sha256",
                "atlas_sha256", "poster_sha256", "atlas_size", "crop_rect", "patches",
                "vertices", "uvs", "triangles", "deformations"))
    _require(rig["recipe_version"] == RECIPE_VERSION and type(rig["topology_version"]) is int
             and rig["topology_version"] == 1 and type(rig["fake_only"]) is bool, "visual_rig_schema")
    for key in ("request_identity_sha256", "atlas_sha256", "poster_sha256"):
        _digest(rig[key])
    _require(rig["atlas_sha256"] == hashlib.sha256(atlas).hexdigest()
             and rig["poster_sha256"] == hashlib.sha256(poster).hexdigest(), "visual_rig_digest")
    size = rig["atlas_size"]
    _require(type(size) is list and len(size) == 2 and all(type(v) is int for v in size), "visual_rig_schema")
    _require(all(512 <= v <= 1024 for v in size), "visual_image_dimensions")
    rect = rig["crop_rect"]
    _require(type(rect) is list and len(rect) == 4 and all(type(v) is int for v in rect), "visual_rig_schema")
    x, y, width, height = rect
    _require(width == height == 512 and 0 <= x <= size[0] - 512
             and 0 <= y <= size[1] - 512, "visual_rig_crop")
    _keys(rig["patches"], _CHANNELS)
    for patch in rig["patches"].values():
        _require(type(patch) is list and len(patch) == 4, "visual_rig_schema")
        px, py, pw, ph = [_number(v, 0, 1) for v in patch]
        _require(pw > 0 and ph > 0 and px + pw <= 1 and py + ph <= 1, "visual_rig_patch")
    vertices, uvs, triangles = rig["vertices"], rig["uvs"], rig["triangles"]
    _require(type(vertices) is list and 4 <= len(vertices) <= MAX_VERTICES, "visual_rig_vertices")
    _require(type(uvs) is list and len(uvs) == len(vertices), "visual_rig_vertices")
    for vertex, uv in zip(vertices, uvs):
        for pair in (vertex, uv):
            _require(type(pair) is list and len(pair) == 2, "visual_rig_schema")
            for value in pair:
                _number(value, 0, 1)
        _require(abs(uv[0] - (x + vertex[0] * 512) / size[0]) < 1e-9
                 and abs(uv[1] - (y + vertex[1] * 512) / size[1]) < 1e-9, "visual_rig_uv")
    _require(len({tuple(v) for v in vertices}) == len(vertices), "visual_rig_topology")
    _require(type(triangles) is list and 2 <= len(triangles) <= MAX_TRIANGLES, "visual_rig_triangles")
    edges, seen, used, areas = {}, set(), set(), []
    for triangle in triangles:
        _require(type(triangle) is list and len(triangle) == 3, "visual_rig_schema")
        _require(all(type(i) is int and 0 <= i < len(vertices) for i in triangle)
                 and len(set(triangle)) == 3, "visual_rig_indices")
        identity = tuple(sorted(triangle))
        _require(identity not in seen, "visual_rig_topology")
        seen.add(identity)
        used.update(triangle)
        area = _area(*(vertices[i] for i in triangle))
        _require(area > 1e-8, "visual_rig_winding")
        areas.append(area)
        for a, b in zip(triangle, triangle[1:] + triangle[:1]):
            edges.setdefault(tuple(sorted((a, b))), []).append((a, b))
    _require(len(used) == len(vertices) and abs(sum(areas) - 2) < 1e-7, "visual_rig_topology")
    for (a, b), directions in edges.items():
        _require(len(directions) in (1, 2), "visual_rig_topology")
        if len(directions) == 2:
            _require(directions[0] == directions[1][::-1], "visual_rig_topology")
        else:
            _require(any(vertices[a][axis] == vertices[b][axis] == side
                         for axis in (0, 1) for side in (0, 1)), "visual_rig_topology")
    # A connected disk with its only boundary on the image prevents holes and
    # disconnected/overlapping islands. Positive orientation covers the square.
    _require(len(vertices) - len(edges) + len(triangles) == 1, "visual_rig_topology")
    reachable, pending = set(), [0]
    neighbors = {i: set() for i in used}
    for a, b in edges:
        neighbors[a].add(b)
        neighbors[b].add(a)
    while pending:
        current = pending.pop()
        if current not in reachable:
            reachable.add(current)
            pending.extend(neighbors[current] - reachable)
    _require(reachable == used, "visual_rig_topology")

    _keys(rig["deformations"], _CHANNELS)
    for channel, displacements in rig["deformations"].items():
        _require(type(displacements) is list and len(displacements) == len(vertices), "visual_rig_schema")
        px, py, pw, ph = rig["patches"][channel]
        for vertex, dy in zip(vertices, displacements):
            _number(dy, -_AMPLITUDES[channel], _AMPLITUDES[channel])
            if dy:
                _require(all(0 < value < 1 for value in vertex), "visual_rig_anchor")
                _require(px < vertex[0] < px + pw and py < vertex[1] < py + ph
                         and py <= vertex[1] + dy <= py + ph, "visual_rig_patch")
        if channel == "mouth":
            _require(max(displacements) - min(displacements) <= 0.02, "visual_rig_envelope")
    # A vertex may belong to at most one patch/channel; avoid mouth/blink sums
    # defeating the independent amplitude/patch constraints.
    for i in range(len(vertices)):
        _require(sum(rig["deformations"][c][i] != 0 for c in _CHANNELS) <= 1, "visual_rig_patch")
    # With vertical additive motion, each triangle's signed area is affine in
    # all channel values. Corner validation therefore bounds the whole envelope;
    # intermediate samples additionally exercise the renderer contract.
    for envelope in itertools.product((0, 0.5, 1), repeat=3):
        posed = [[vx, vy + sum(envelope[j] * rig["deformations"][c][i]
                              for j, c in enumerate(_CHANNELS))]
                 for i, (vx, vy) in enumerate(vertices)]
        _require(all(0 <= p[1] <= 1 for p in posed), "visual_rig_envelope")
        for triangle, neutral_area in zip(triangles, areas):
            area = _area(*(posed[i] for i in triangle))
            _require(0.5 * neutral_area <= area <= 1.5 * neutral_area, "visual_rig_envelope")
    return rig


def validate_bundle(bundle: VisualAssetBundle) -> list[dict]:
    """Return verified descriptors in role order, or raise VisualBundleError.

    Checks the exact bytes without silently rewriting them. Rig dimensions are
    None. These descriptors confer no authorization or permission to publish.
    """
    _require(isinstance(bundle, VisualAssetBundle) and type(bundle.assets) is dict, "visual_bundle_roles")
    assets = bundle.assets.copy()
    _require(set(assets) == set(ASSET_ROLES), "visual_bundle_roles")
    _require(all(type(v) is bytes and len(v) > 0 for v in assets.values()), "visual_bundle_bytes")
    _require(sum(map(len, assets.values())) <= MAX_BUNDLE_BYTES, "visual_bundle_too_large")
    _require(len(assets["rig"]) <= MAX_RIG_BYTES and len(assets["poster"]) <= MAX_POSTER_BYTES,
             "visual_bundle_too_large")
    rig = _validate_rig(assets["rig"], assets["texture_atlas"], assets["poster"])
    dimensions = {}
    for role in ("poster", "texture_atlas"):
        with _png(assets[role], poster=role == "poster") as decoded:
            dimensions[role] = decoded.size
    _require(list(dimensions["texture_atlas"]) == rig["atlas_size"], "visual_image_dimensions")
    return [{"logical_role": role, "mime_type": "application/json" if role == "rig" else "image/png",
             "byte_size": len(assets[role]), "sha256": hashlib.sha256(assets[role]).hexdigest(),
             "width": dimensions.get(role, (None, None))[0],
             "height": dimensions.get(role, (None, None))[1]} for role in ASSET_ROLES]


def _request_digest(data, identity):
    # Only bounded server-owned digests/counters are accepted; never echo the
    # caller's dictionary into public rig JSON. Worker/domain own scoped IDs.
    allowed = {"source_sha256", "request_digest", "recipe_version", "model_digest",
               "source_generation", "source_artifact_generation"}
    _require(type(identity) is dict and not set(identity) - allowed, "visual_request_identity")
    for key, value in identity.items():
        if key == "recipe_version":
            _require(value == RECIPE_VERSION, "visual_request_identity")
        elif key.endswith("generation"):
            _require(type(value) is int and 1 <= value <= 2**31 - 1, "visual_request_identity")
        else:
            _digest(value)
    if "source_sha256" in identity:
        _require(identity["source_sha256"] == hashlib.sha256(data).hexdigest(), "visual_request_identity")
    return hashlib.sha256(_json_bytes(identity)).hexdigest()


class FakePortraitRigProvider:
    """Deterministic lifecycle fixture; square-grid weights are NOT landmarks.

    Uses only the approved crop's pixels. The synthetic motion/patch positions
    must never be presented as evidence that any subject is riggable or natural.
    There is no detector, model download, network, storage or database access.
    """

    provider_name = "fake-test-only"
    model_digest = hashlib.sha256(b"l19-fake-test-only").hexdigest()
    recipe_version = RECIPE_VERSION
    fake_only = True

    def prepare(self, data: bytes, crop: dict, request_identity: dict) -> VisualAssetBundle:
        _require(type(data) is bytes and 0 < len(data) <= 20 * 1024 * 1024, "visual_image_invalid")
        _require(type(crop) is dict, "visual_crop_invalid")
        request_digest = _request_digest(data, request_identity)
        normalized = normalize_crop(data, crop)
        from PIL import Image

        with _png(normalized) as decoded:
            _require(decoded.size == (512, 512), "visual_image_dimensions")
            atlas = _encode_png(decoded)
            with decoded.resize((256, 256), Image.Resampling.LANCZOS) as resized:
                poster = _encode_png(resized)
        # Nine by nine grid: 81 vertices and 128 positively wound triangles.
        vertices = [[x / 8, y / 8] for y in range(9) for x in range(9)]
        triangles = []
        for y in range(8):
            for x in range(8):
                a = y * 9 + x
                triangles.extend(([a, a + 1, a + 10], [a, a + 10, a + 9]))
        patches = {"mouth": [0.25, 0.625, 0.5, 0.25],
                   "blink_left": [0.125, 0.25, 0.25, 0.25],
                   "blink_right": [0.625, 0.25, 0.25, 0.25]}
        movements = {c: [0.0] * len(vertices) for c in _CHANNELS}
        for channel, (px, py, pw, ph) in patches.items():
            for i, (vx, vy) in enumerate(vertices):
                if px < vx < px + pw and py < vy < py + ph:
                    weight = (1 - abs((vx - px) / pw * 2 - 1)) * (1 - abs((vy - py) / ph * 2 - 1))
                    movements[channel][i] = round(0.01 * weight, 8)
        rig = {"recipe_version": RECIPE_VERSION, "topology_version": 1, "fake_only": True,
               "request_identity_sha256": request_digest, "atlas_sha256": hashlib.sha256(atlas).hexdigest(),
               "poster_sha256": hashlib.sha256(poster).hexdigest(), "atlas_size": [512, 512],
               "crop_rect": [0, 0, 512, 512], "patches": patches,
               "vertices": vertices, "uvs": vertices, "triangles": triangles, "deformations": movements}
        bundle = VisualAssetBundle({"poster": poster, "texture_atlas": atlas, "rig": _json_bytes(rig)})
        validate_bundle(bundle)
        return bundle
