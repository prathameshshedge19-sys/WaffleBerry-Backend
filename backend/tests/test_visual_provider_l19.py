"""Pixel-only synthetic fixtures. No face, imagegen or real-quality acceptance."""

import copy
import hashlib
import io
import itertools
import json
import struct
import zlib

import pytest
from PIL import Image, PngImagePlugin

from app.services import visual_provider as vp
from app.services import visual_reference as reference


FULL_CROP = {"x": 0, "y": 0, "width": 1, "height": 1, "rotation": 0}


def png(size=(512, 512), color=(40, 90, 140), **save_args):
    with Image.new("RGB", size, color) as pixels:
        output = io.BytesIO()
        pixels.save(output, format="PNG", **save_args)
        return output.getvalue()


@pytest.fixture
def source():
    return png()


@pytest.fixture
def provider(monkeypatch):
    # EXPLICIT test-only bypass: _decode_image is never a production fallback.
    # Every byte passed to this fixture is generated locally by these tests.
    monkeypatch.setattr(vp, "normalize_crop", reference._decode_image)
    return vp.FakePortraitRigProvider()


@pytest.fixture
def bundle(provider, source):
    return provider.prepare(source, FULL_CROP, {"source_sha256": hashlib.sha256(source).hexdigest()})


def rig_of(bundle):
    return json.loads(bundle.assets["rig"])


def with_rig(bundle, rig):
    return vp.VisualAssetBundle({**bundle.assets, "rig": json.dumps(rig, separators=(",", ":")).encode()})


def with_asset(bundle, role, data):
    assets = {**bundle.assets, role: data}
    rig = rig_of(bundle)
    if role != "rig":
        rig[role.replace("texture_atlas", "atlas") + "_sha256"] = hashlib.sha256(data).hexdigest()
        assets["rig"] = json.dumps(rig, separators=(",", ":")).encode()
    return vp.VisualAssetBundle(assets)


def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)


def raw_png(raw_pixels, width=256, height=256, extra_compressed=b""):
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw_pixels) + extra_compressed) + chunk(b"IEND", b""))


def test_provider_contract_determinism_and_verified_descriptors(provider, source, bundle):
    assert provider.provider_name == "fake-test-only"
    assert provider.model_digest == hashlib.sha256(b"l19-fake-test-only").hexdigest()
    assert provider.recipe_version == "portrait_2d_v1"
    assert provider.fake_only is True
    identity = {"source_sha256": hashlib.sha256(source).hexdigest()}
    assert provider.prepare(source, FULL_CROP, identity) == bundle
    specs = vp.validate_bundle(bundle)
    assert [s["logical_role"] for s in specs] == ["poster", "texture_atlas", "rig"]
    assert [(s["width"], s["height"]) for s in specs] == [(256, 256), (512, 512), (None, None)]
    for spec in specs:
        assert set(spec) == {"logical_role", "mime_type", "byte_size", "sha256", "width", "height"}
        data = bundle.assets[spec["logical_role"]]
        assert spec["sha256"] == hashlib.sha256(data).hexdigest()
        assert spec["byte_size"] == len(data)
        assert spec["mime_type"] == ("application/json" if spec["logical_role"] == "rig" else "image/png")
    rig = rig_of(bundle)
    assert rig["fake_only"] is True
    assert len(rig["vertices"]) == 81 and len(rig["triangles"]) == 128
    assert rig["crop_rect"] == [0, 0, 512, 512]
    assert sum(map(len, bundle.assets.values())) <= 2 * 1024 * 1024
    assert b"fake-test-only" not in bundle.assets["rig"]  # No provider metadata in delivered JSON.


def test_prepare_uses_public_confined_decoder_and_propagates_failure(monkeypatch, source):
    assert vp.normalize_crop is reference.normalize_crop
    def unavailable(data, crop):
        assert data is source and crop is FULL_CROP
        raise reference.VisualReferenceError("visual_decoder_isolation_unavailable")
    monkeypatch.setattr(vp, "normalize_crop", unavailable)
    with pytest.raises(reference.VisualReferenceError, match="visual_decoder_isolation_unavailable"):
        vp.FakePortraitRigProvider().prepare(source, FULL_CROP, {})


def test_no_real_provider_or_database_dependency():
    assert not hasattr(vp, "LocalPortraitRigProvider")
    assert not any(name in vars(vp) for name in ("Session", "Legacy", "requests", "SourceEvidence"))


def test_only_selected_pixels_survive_and_metadata_is_stripped(provider):
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Comment", "private-name https://invalid.test/location")
    with Image.new("RGB", (1024, 512), "red") as pixels:
        pixels.paste("blue", (512, 0, 1024, 512))
        output = io.BytesIO()
        pixels.save(output, format="PNG", pnginfo=metadata)
    crop = {"x": 0.5, "y": 0, "width": 0.5, "height": 1, "rotation": 0}
    result = provider.prepare(output.getvalue(), crop, {})
    vp.validate_bundle(result)
    for role in ("poster", "texture_atlas"):
        with Image.open(io.BytesIO(result.assets[role])) as image:
            image.load()
            assert not image.info
            assert image.getextrema() == ((0, 0), (0, 0), (255, 255))
    assert all(b"private-name" not in data for data in result.assets.values())


def test_request_binding_is_bounded_and_never_echoes_identity(provider, source):
    identity = {"source_sha256": hashlib.sha256(source).hexdigest(), "request_digest": "b" * 64,
                "recipe_version": vp.RECIPE_VERSION, "model_digest": provider.model_digest,
                "source_generation": 1, "source_artifact_generation": 1}
    saved = copy.deepcopy(identity)
    result = provider.prepare(source, FULL_CROP, identity)
    expected = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert rig_of(result)["request_identity_sha256"] == expected
    assert identity == saved
    assert b"source_sha256" not in result.assets["rig"]
    identity["source_generation"] = 2
    assert provider.prepare(source, FULL_CROP, identity).assets["rig"] != result.assets["rig"]


@pytest.mark.parametrize("identity", [[], {"subject_name": "Name"}, {"url": "https://bad.test"},
    {"auth_token": "secret"}, {"request_digest": "x" * 64}, {"source_sha256": "0" * 64},
    {"recipe_version": "v2"}, {"source_generation": True}, {"source_generation": 0},
    {"source_artifact_generation": 2**40}, {"model_digest": {}}, {"request_digest": "A" * 64}])
def test_invalid_request_identity(provider, source, identity):
    with pytest.raises(vp.VisualBundleError):
        provider.prepare(source, FULL_CROP, identity)


@pytest.mark.parametrize("crop", [None, {}, {**FULL_CROP, "x": -0.1}, {**FULL_CROP, "x": float("nan")},
    {**FULL_CROP, "width": 0.1}, {**FULL_CROP, "width": 0}, {**FULL_CROP, "rotation": 45},
    {**FULL_CROP, "url": "https://bad.test"}])
def test_invalid_crop_is_rejected(provider, source, crop):
    with pytest.raises((reference.VisualReferenceError, vp.VisualBundleError)):
        provider.prepare(source, crop, {})


@pytest.mark.parametrize("data", [b"", b"<svg onload='alert(1)'/>", b"GIF89a", b"\xff\xd8broken", b"x" * (20 * 1024 * 1024 + 1)],
                         ids=["empty", "svg", "gif", "broken-jpeg", "oversized"])
def test_invalid_source(provider, data):
    with pytest.raises((reference.VisualReferenceError, vp.VisualBundleError)):
        provider.prepare(data, FULL_CROP, {})


@pytest.mark.parametrize("change", ["missing", "extra", "url", "bytearray", "empty", "wrong-container"])
def test_exact_role_inventory_and_bytes(bundle, change):
    assets = dict(bundle.assets)
    if change == "missing":
        assets.pop("rig")
    elif change == "extra":
        assets["script"] = b"alert(1)"
    elif change == "url":
        assets["poster"] = "https://bad.test/portrait.png"
    elif change == "bytearray":
        assets["poster"] = bytearray(assets["poster"])
    elif change == "empty":
        assets["poster"] = b""
    else:
        assets = list(assets.items())
    with pytest.raises(vp.VisualBundleError):
        vp.validate_bundle(vp.VisualAssetBundle(assets))


@pytest.mark.parametrize("role,size", [("poster", 100 * 1024 + 1), ("rig", 128 * 1024 + 1),
                                      ("texture_atlas", 2 * 1024 * 1024)])
def test_encoded_budgets_before_decode(bundle, role, size):
    bad = vp.VisualAssetBundle({**bundle.assets, role: b"x" * size})
    with pytest.raises(vp.VisualBundleError, match="visual_bundle_too_large"):
        vp.validate_bundle(bad)


@pytest.mark.parametrize("raw", [b"{}", b"[]", b"null", b"\xff", b"{", b"<script>alert(1)</script>",
    b'{"recipe_version":"portrait_2d_v1","recipe_version":"portrait_2d_v1"}', b"[" * 2000 + b"]" * 2000])
def test_json_failures_are_sanitized(bundle, raw):
    with pytest.raises(vp.VisualBundleError, match="^visual_"):
        vp.validate_bundle(with_asset(bundle, "rig", raw))


@pytest.mark.parametrize("where,key,value", [(None, "script", "alert(1)"), (None, "url", "https://bad.test"),
    (None, "subject_name", "name"), ("patches", "extra", [0, 0, 1, 1]),
    ("deformations", "expression", "y += energy"), (None, "__proto__", {}),
    (None, "recipe_version", "javascript:alert(1)"), (None, "fake_only", "true"),
    (None, "topology_version", True)])
def test_strict_schema_at_every_object(bundle, where, key, value):
    rig = rig_of(bundle)
    (rig if where is None else rig[where])[key] = value
    with pytest.raises(vp.VisualBundleError):
        vp.validate_bundle(with_rig(bundle, rig))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), True, "0.5", None, [], {}, -0.01, 1.01, 10**500])
def test_numeric_values_are_finite_and_strict(bundle, value):
    rig = rig_of(bundle)
    rig["vertices"][10][0] = value
    with pytest.raises(vp.VisualBundleError):
        vp.validate_bundle(with_rig(bundle, rig))


@pytest.mark.parametrize("kind", ["vertices", "triangles", "uvs", "unused", "duplicate", "reverse", "degenerate", "hole", "index", "bool-index"])
def test_mesh_limits_topology_winding_and_indices(bundle, kind):
    rig = rig_of(bundle)
    if kind == "vertices":
        rig["vertices"] = [[0, 0]] * 513
    elif kind == "triangles":
        rig["triangles"] *= 9
    elif kind == "uvs":
        rig["uvs"][10][0] = 0.9
    elif kind == "unused":
        rig["vertices"].append([0.3, 0.3])
        rig["uvs"].append([0.3, 0.3])
    elif kind == "duplicate":
        rig["triangles"].append(rig["triangles"][0])
    elif kind == "reverse":
        rig["triangles"][0].reverse()
    elif kind == "degenerate":
        rig["triangles"][0] = [0, 1, 2]
    elif kind == "hole":
        rig["triangles"].pop(40)
    elif kind == "index":
        rig["triangles"][0][0] = 81
    else:
        rig["triangles"][0][0] = False
    with pytest.raises(vp.VisualBundleError):
        vp.validate_bundle(with_rig(bundle, rig))


@pytest.mark.parametrize("rect", [[0, 0, 511, 512], [1, 0, 512, 512], [-1, 0, 512, 512],
                                 [0, 0, 512.0, 512], [False, 0, 512, 512]])
def test_full_normalized_crop_bounds(bundle, rect):
    rig = rig_of(bundle)
    rig["crop_rect"] = rect
    with pytest.raises(vp.VisualBundleError):
        vp.validate_bundle(with_rig(bundle, rig))


@pytest.mark.parametrize("patch", [[0.9, 0.9, 0.2, 0.2], [0, 0, 0, 1], [0, -0.1, 1, 1],
                                  [0, 0, float("inf"), 1], [0, 0, 0.1, 0.1], [0, 0, 1]])
def test_patch_bounds_and_displacement_support(bundle, patch):
    rig = rig_of(bundle)
    rig["patches"]["mouth"] = patch
    with pytest.raises(vp.VisualBundleError):
        vp.validate_bundle(with_rig(bundle, rig))


@pytest.mark.parametrize("kind", ["amplitude", "anchor", "length", "nonfinite", "overlap"])
def test_channel_envelope_bounds(bundle, kind):
    rig = rig_of(bundle)
    mouth = rig["deformations"]["mouth"]
    if kind == "amplitude":
        mouth[58] = 0.01501
    elif kind == "anchor":
        mouth[0] = 0.001
    elif kind == "length":
        mouth.pop()
    elif kind == "nonfinite":
        mouth[58] = float("nan")
    else:
        rig["patches"]["blink_left"] = rig["patches"]["mouth"]
        rig["deformations"]["blink_left"] = mouth[:]
    with pytest.raises(vp.VisualBundleError):
        vp.validate_bundle(with_rig(bundle, rig))


def test_neutral_blink_mouth_combined_endpoints_and_intermediates(bundle):
    rig = rig_of(bundle)
    for channel in ("mouth", "blink_left", "blink_right"):
        assert any(rig["deformations"][channel])  # A static grid cannot prove motion validation.
    for mouth, left, right in itertools.product((0, 0.25, 0.5, 0.75, 1), repeat=3):
        posed = []
        for i, (x, y) in enumerate(rig["vertices"]):
            dy = (mouth * rig["deformations"]["mouth"][i] + left * rig["deformations"]["blink_left"][i]
                  + right * rig["deformations"]["blink_right"][i])
            assert abs(dy) <= 0.015
            assert 0 <= y + dy <= 1
            posed.append((x, y + dy))
            if x in (0, 1) or y in (0, 1):
                assert dy == 0
        for indices in rig["triangles"]:
            a, b, c = (posed[i] for i in indices)
            assert (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]) > 0


def test_endpoint_inversion_rejected_even_when_neutral_winding_and_amplitude_pass(bundle):
    rig = rig_of(bundle)
    # Compress two interior grid rows to 0.01 separation, then displace the
    # upper row through the lower at mouth=1. Neutral topology stays valid.
    for i, vertex in enumerate(rig["vertices"]):
        if vertex[1] == 0.625:
            vertex[1] = 0.74
            rig["uvs"][i][1] = 0.74
    rig["patches"]["mouth"] = [0.125, 0.5, 0.75, 0.4]
    rig["deformations"]["mouth"] = [0.0] * len(rig["vertices"])
    neutral = with_rig(bundle, rig)
    vp.validate_bundle(neutral)
    rig["deformations"]["mouth"][5 * 9 + 4] = 0.015
    with pytest.raises(vp.VisualBundleError, match="visual_rig_envelope"):
        vp.validate_bundle(with_rig(bundle, rig))


def test_mouth_aperture_cannot_exceed_two_percent(bundle):
    rig = rig_of(bundle)
    rig["deformations"]["mouth"][57] = -0.011
    rig["deformations"]["mouth"][58] = 0.011
    with pytest.raises(vp.VisualBundleError, match="visual_rig_envelope"):
        vp.validate_bundle(with_rig(bundle, rig))


@pytest.mark.parametrize("role", ["poster", "texture_atlas"])
def test_raster_digest_binding(bundle, role):
    assets = {**bundle.assets, role: png((256, 256) if role == "poster" else (512, 512), color="white")}
    with pytest.raises(vp.VisualBundleError, match="visual_rig_digest"):
        vp.validate_bundle(vp.VisualAssetBundle(assets))


@pytest.mark.parametrize("kind", ["text", "exif", "icc", "unknown", "trailing", "svg", "jpeg", "crc", "truncated", "animated"])
def test_raster_rejects_metadata_executable_types_and_corruption(bundle, kind):
    data = bundle.assets["poster"]
    if kind == "text":
        info = PngImagePlugin.PngInfo()
        info.add_text("Comment", "<script src='https://bad.test'/>")
        data = png((256, 256), pnginfo=info)
    elif kind in ("exif", "icc", "unknown"):
        tag = {"exif": b"eXIf", "icc": b"iCCP", "unknown": b"vpAg"}[kind]
        data = data[:33] + chunk(tag, b"hidden metadata") + data[33:]
    elif kind == "trailing":
        data += b"<script>alert(1)</script>"
    elif kind == "svg":
        data = b"<svg xmlns='http://www.w3.org/2000/svg' onload='alert(1)'/>"
    elif kind == "jpeg":
        with Image.new("RGB", (256, 256)) as pixels:
            output = io.BytesIO()
            pixels.save(output, format="JPEG")
            data = output.getvalue()
    elif kind == "crc":
        data = data[:29] + b"\0\0\0\0" + data[33:]
    elif kind == "truncated":
        data = data[:-12]
    else:
        data = data[:33] + chunk(b"acTL", struct.pack(">II", 2, 0)) + data[33:]
    with pytest.raises(vp.VisualBundleError):
        vp.validate_bundle(with_asset(bundle, "poster", data))


@pytest.mark.parametrize("kind", ["too-many-pixels", "short-pixels", "bad-filter", "unused-zlib", "bad-zlib", "big-header"])
def test_bounded_full_decompression(bundle, kind):
    pixels = (b"\0" + bytes(256 * 3)) * 256
    if kind == "too-many-pixels":
        data = raw_png(pixels + b"x" * 1000000)
    elif kind == "short-pixels":
        data = raw_png(pixels[:-1])
    elif kind == "bad-filter":
        data = raw_png(b"\x05" + pixels[1:])
    elif kind == "unused-zlib":
        data = raw_png(pixels, extra_compressed=zlib.compress(b"hidden script"))
    elif kind == "bad-zlib":
        data = (bundle.assets["poster"][:33] + chunk(b"IDAT", b"invalid zlib") + chunk(b"IEND", b""))
    else:
        data = raw_png(pixels, width=100000, height=100000)
    with pytest.raises(vp.VisualBundleError):
        vp.validate_bundle(with_asset(bundle, "poster", data))


@pytest.mark.parametrize("role,size", [("poster", (255, 256)), ("poster", (512, 512)),
    ("texture_atlas", (1025, 512)), ("texture_atlas", (511, 512)), ("texture_atlas", (513, 512))])
def test_raster_dimensions_match_recipe_and_rig(bundle, role, size):
    with pytest.raises(vp.VisualBundleError):
        vp.validate_bundle(with_asset(bundle, role, png(size)))


def test_provider_neutral_validator_accepts_bounded_atlas_layout(bundle):
    # Alternate byte producer/layout, no real-provider implementation or claim.
    result = with_asset(bundle, "texture_atlas", png((1024, 1024)))
    rig = rig_of(result)
    rig["atlas_size"] = [1024, 1024]
    rig["crop_rect"] = [256, 128, 512, 512]
    rig["uvs"] = [[(256 + x * 512) / 1024, (128 + y * 512) / 1024] for x, y in rig["vertices"]]
    specs = vp.validate_bundle(with_rig(result, rig))
    assert (specs[1]["width"], specs[1]["height"]) == (1024, 1024)


def test_validation_preserves_exact_bytes(bundle):
    before = copy.deepcopy(bundle.assets)
    assert vp.validate_bundle(bundle) == vp.validate_bundle(bundle)
    assert bundle.assets == before
