"""Future-write canonical perspective without runtime or retrieval coupling."""

from decimal import Decimal
from types import SimpleNamespace

from app.models.memory import IdentityFactType, Legacy, MemoryType
from app.schemas.memory import (
    MemoryCandidateCreate, MemoryDetails, MemoryParticipantCreate,
    MemoryProvenanceCreate,
)
from app.services.memory.canonical_perspective import (
    CanonicalMemoryPerspectiveService,
)


SOURCE = "Anjali and her younger brother Aditya grew up together in Pune."


def candidate(summary, participants, *, excerpt=SOURCE, tags=("family",)):
    return MemoryCandidateCreate(
        memory_type=MemoryType.NARRATIVE, category="story", title=summary,
        summary=summary, details=MemoryDetails(), importance=5,
        extraction_confidence=Decimal("0.99"), participants=participants,
        tags=list(tags), provenance=[MemoryProvenanceCreate(
            source_type="manual", excerpt=excerpt, speaker="user",
        )],
    )


def person(name, relationship=None, role="mentioned_person"):
    return MemoryParticipantCreate(
        name=name, relationship=relationship, role=role,
    )


def normalize(item, *, facts=(), memories=()):
    legacy = Legacy(display_name="Anjali Deshmukh", relationship="self", owner_user_id=1)
    return CanonicalMemoryPerspectiveService().normalize(
        item, legacy=legacy, identity_facts=facts, existing_memories=memories,
    )


def test_childhood_uses_resolved_self_relationship_and_preserves_source():
    item = candidate(
        "Anjali and her younger brother Aditya grew up together in Pune. "
        "Anjali used to tease Aditya when they were children, but they were very close.",
        [person("Anjali", role="subject"), person("Aditya", "younger brother")],
    )
    result = normalize(item)
    assert result.summary.startswith("My younger brother Aditya and I grew up together")
    assert "I used to tease Aditya" in result.summary
    assert "we were children" in result.summary
    assert "we were very close" in result.summary
    assert result.provenance[0].excerpt == SOURCE


def test_existing_first_person_input_stays_first_person():
    text = "My younger brother Aditya and I grew up together in Pune."
    result = normalize(candidate(text, [person("Aditya", "younger brother")], excerpt=text))
    assert result.summary == text


def test_other_people_never_become_self():
    text = "Mohan and Aditya grew up together in Pune."
    result = normalize(candidate(text, [person("Mohan"), person("Aditya")], excerpt=text))
    assert result.summary == text


def test_household_tv_uses_we():
    text = "Anjali's family has an 85-inch TV."
    result = normalize(candidate(
        text, [person("Anjali", role="subject")], excerpt=text,
        tags=("household", "tv"),
    ))
    assert result.summary == "We have an 85-inch TV."


def test_pet_names_use_existing_canonical_entities():
    text = "Anjali's family has Labradors named bruno and luffy."
    known = SimpleNamespace(participants=[
        SimpleNamespace(name="Bruno"), SimpleNamespace(name="Luffy"),
    ])
    result = normalize(candidate(
        text,
        [person("Anjali", role="subject"), person("bruno"), person("luffy")],
        excerpt=text, tags=("household", "pets"),
    ), memories=(known,))
    assert result.summary == "We have 2 Labradors, Bruno and Luffy."


def test_marathi_provenance_is_exact_while_canonical_text_is_english_first_person():
    source = "मी आणि माझा भाऊ Aditya पुण्यात एकत्र मोठे झालो."
    item = candidate(
        "Anjali and Aditya grew up together in Pune.",
        [person("Anjali", role="subject"), person("Aditya", "brother")],
        excerpt=source,
    )
    result = normalize(item)
    assert result.summary == "Aditya and I grew up together in Pune."
    assert result.provenance[0].excerpt == source


def test_existing_identity_claim_is_not_duplicated():
    fact = SimpleNamespace(
        fact_type=IdentityFactType.SIBLING_NAME, value="Aditya Deshmukh",
        relationship="younger brother",
    )
    details = MemoryDetails(identity_facts=[{
        "fact_type": "sibling_name", "value": "Aditya Deshmukh",
        "relationship": "younger brother", "confidence": 1,
    }])
    item = candidate(
        "Anjali and Aditya Deshmukh grew up together in Pune.",
        [person("Anjali", role="subject"), person("Aditya Deshmukh", "younger brother")],
    ).model_copy(update={"details": details})
    result = normalize(item, facts=(fact,))
    assert result.details.identity_facts == []
    assert "Aditya Deshmukh and I" in result.summary
