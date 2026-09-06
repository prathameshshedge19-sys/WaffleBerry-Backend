"""JSON persistence restores manifest IDs without relaxing evidence validation."""

from dataclasses import replace
import json

import pytest
from pydantic import ValidationError

from app.models.personality import LegacyPersonalityProfile as Row
from app.schemas.personality import PersonalityProfile
from app.services.legacy_personality import current_profile, derive_profile, load_evidence, validate_profile
from tests.test_personality_l13 import evidence, saved  # noqa: F401


def test_integer_manifest_ids_survive_json_database_round_trip(saved):
    _client, sessions, _provider, _auth, conversation, memory_id = saved
    legacy_id = conversation["legacy_id"]
    with sessions.begin() as db:
        row = db.get(Row, legacy_id)
        memories = load_evidence(db, legacy_id)
        original = derive_profile(legacy_id, row.source_generation, memories)
        assert all(type(key) is int for key in original.evidence_manifest)
        serialized = json.loads(original.model_dump_json())
        assert str(memory_id) in serialized["evidence_manifest"]
        row.profile_json = serialized
        row.built_generation = row.source_generation
        row.build_status = "ready"

    with sessions() as db:
        persisted = db.get(Row, legacy_id).profile_json
        assert all(type(key) is str for key in persisted["evidence_manifest"])
        restored = validate_profile(persisted, legacy_id, original.source_generation, memories)
        assert restored == original
        assert all(type(key) is int for key in restored.evidence_manifest)
        assert restored.evidence_manifest[memory_id] == original.evidence_manifest[memory_id]
        assert current_profile(db, legacy_id) == original
        # Validation must not mutate the persisted JSON object in place.
        assert all(type(key) is str for key in persisted["evidence_manifest"])


@pytest.mark.parametrize("key", ["not-an-id", "1e2", "1.0", "-1", "0", "01", " 1", "1 ", "", "\u0661", True, 1.0])
def test_malformed_or_noninteger_manifest_keys_are_rejected(key):
    with pytest.raises(ValidationError):
        PersonalityProfile.model_validate({
            "legacy_id": 1, "source_generation": 1,
            "evidence_manifest": {key: "0" * 64},
        })


def test_numeric_string_and_integer_collision_is_rejected():
    with pytest.raises(ValidationError, match="Duplicate normalized manifest ID"):
        PersonalityProfile(legacy_id=1, source_generation=1, evidence_manifest={1: "0" * 64, "1": "1" * 64})


def test_normalization_does_not_relax_fingerprint_validation():
    with pytest.raises(ValidationError):
        PersonalityProfile(legacy_id=1, source_generation=1, evidence_manifest={"1": "invalid-fingerprint"})


def test_normalization_does_not_coerce_evidence_reference_ids():
    memory = evidence()
    serialized = json.loads(derive_profile(1, 1, (memory,)).model_dump_json())
    serialized["observations"][0]["evidence"][0]["memory_id"] = "1"
    with pytest.raises(ValidationError):
        validate_profile(serialized, 1, 1, (memory,))


def test_cross_legacy_evidence_remains_rejected_after_json_round_trip():
    memory = evidence()
    serialized = json.loads(derive_profile(1, 1, (memory,)).model_dump_json())
    with pytest.raises(ValueError, match="scope/generation mismatch"):
        validate_profile(serialized, 1, 1, (replace(memory, legacy_id=2),))
    with pytest.raises(ValueError, match="scope/generation mismatch"):
        validate_profile(serialized, 2, 1, (memory,))


def test_empty_profile_json_round_trip_remains_valid():
    original = derive_profile(1, 1, ())
    serialized = json.loads(original.model_dump_json())
    assert validate_profile(serialized, 1, 1, ()) == original
    assert PersonalityProfile.model_validate(serialized).evidence_manifest == {}
