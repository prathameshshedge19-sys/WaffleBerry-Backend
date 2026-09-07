"""Regression for live schema-valid output rejected by strict source DTO enums."""
import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.models.legacy import Legacy
from app.schemas.media_intelligence import SourceAnalysis, SourceEvidenceInput
from app.services.media_intelligence import OpenAISourceAnalysisProvider, SourceProviderError, _analysis_schema
from app.services.memory import MEMORY_CATEGORIES, ENTITY_TYPES


def valid_response():
    return {'source_language': 'english', 'summary': None, 'candidates': [{
        'canonical_text': 'A synthetic subject enjoys gardening.', 'category': 'preference',
        'confidence': .9, 'evidence_indexes': [0], 'uncertainty': None,
        'source_language': 'english', 'entities': [
            {'name': 'Synthetic subject', 'entity_type': 'person', 'role': 'subject', 'aliases': []},
            {'name': 'Gardening', 'entity_type': 'preference', 'role': 'interest', 'aliases': []},
        ]} for _ in range(3)]}


def rejected_live_shape():
    # Retain observed paths/types/lengths, not source text or response bodies.
    value = valid_response()
    for index, length in enumerate((15, 26, 15)):
        value['candidates'][index]['category'] = 'x' * length
    for index, entity_index, length in ((0, 1, 5), (1, 1, 6), (2, 2, 4)):
        entities = value['candidates'][index]['entities']
        if entity_index == 2:
            entities.append(copy.deepcopy(entities[1]))
        entities[entity_index]['entity_type'] = 'x' * length
    return value


def test_request_schema_closes_all_six_observed_enum_gaps():
    payload = rejected_live_shape()
    with pytest.raises(ValidationError) as failed:
        SourceAnalysis.model_validate(payload)
    candidate = _analysis_schema()['properties']['candidates']['items']['properties']
    for error in failed.value.errors():
        field = error['loc'][-1]
        schema = candidate['category'] if field == 'category' else candidate['entities']['items']['properties']['entity_type']
        assert 'enum' in schema, f"Provider contract permits server-rejected {field}"
        assert error['input'] not in schema['enum']
    assert len(failed.value.errors()) == 6
    assert candidate['category']['enum'] == list(MEMORY_CATEGORIES)
    assert candidate['entities']['items']['properties']['entity_type']['enum'] == list(ENTITY_TYPES)


def provider_with(payload):
    provider = object.__new__(OpenAISourceAnalysisProvider)
    provider.model = 'test-model'
    provider.client = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(output_text=json.dumps(payload)))))
    return provider


@pytest.mark.parametrize('image', [False, True])
def test_document_and_image_use_same_enum_contract(image):
    provider = provider_with(valid_response())
    legacy = Legacy(id=1, subject_name='Synthetic subject')
    if image:
        result = asyncio.run(provider.analyze_image(legacy, b'synthetic image bytes', 'image/png'))
    else:
        result = asyncio.run(provider.analyze(legacy, 'document', [SourceEvidenceInput(kind='text_span')]))
    assert len(result.candidates) == 3
    request = provider.client.responses.create.call_args.kwargs
    assert request['text']['format']['schema'] == _analysis_schema()
    assert request['text']['format']['strict'] is True and request['store'] is False
    assert 'tools' not in request


@pytest.mark.parametrize('mutation', [
    lambda p: p['candidates'][0].update(category='not_a_category'),
    lambda p: p['candidates'][0]['entities'][0].update(entity_type='not_an_entity'),
    lambda p: p['candidates'][0].update(confidence=1.01),
    lambda p: p['candidates'][0].update(confidence={'score': .9}),
    lambda p: p['candidates'][0].update(evidence_indexes=[True]),
    lambda p: p['candidates'][0].update(evidence_indexes=[-1]),
    lambda p: p['candidates'][0].update(evidence_indexes=[32]),
    lambda p: p['candidates'][0].update(category=None),
    lambda p: p['candidates'][0].update(entities=None),
    lambda p: p['candidates'][0].update(date='invented date'),
    lambda p: p['candidates'][0].update(location={'lat': 12}),
    lambda p: p['candidates'][0].update(locator={'page': 'one'}),
])
def test_malformed_provider_variants_still_fail_closed(mutation):
    payload = valid_response(); mutation(payload)
    with pytest.raises(SourceProviderError) as failed:
        asyncio.run(provider_with(payload).analyze(Legacy(id=1), 'document', []))
    assert failed.value.code == 'source_provider_invalid_response'
    assert isinstance(failed.value.__cause__, ValidationError)


@pytest.mark.parametrize('image', [False, True])
def test_validation_diagnostics_never_log_response_values_or_unknown_keys(caplog, image):
    payload = valid_response()
    payload['candidates'][0]['category'] = 'PRIVATE_SOURCE_CONTENT'
    payload['PRIVATE_UNKNOWN_PROPERTY'] = 'PRIVATE_RESPONSE_BODY'
    provider = provider_with(payload)
    with pytest.raises(SourceProviderError) as failed:
        if image:
            asyncio.run(provider.analyze_image(Legacy(id=1), b'image', 'image/png'))
        else:
            asyncio.run(provider.analyze(Legacy(id=1), 'document', []))
    diagnostics = failed.value.validation_diagnostics
    category = next(item for item in diagnostics if item['path'] == ['candidates', 0, 'category'])
    assert category['path'] == ['candidates', 0, 'category']
    assert category['error_type'] == 'value_error'
    assert category['expected'] == {'type': 'string', 'enum': list(MEMORY_CATEGORIES)}
    assert category['received'] == {'type': 'str', 'length': 22, 'enum_member': False}
    assert any(item['path'] == ['<unknown_field>'] for item in diagnostics)
    assert 'media_provider_validation' in caplog.text
    assert 'PRIVATE' not in caplog.text and 'Synthetic subject' not in caplog.text
    assert 'PRIVATE' not in json.dumps(diagnostics)
