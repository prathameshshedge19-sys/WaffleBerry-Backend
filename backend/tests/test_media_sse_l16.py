"""SSE-C environment encoding must preserve exactly 32 random key bytes."""
import base64
from types import SimpleNamespace
from unittest.mock import Mock

import boto3
from botocore.handlers import _sse_md5
import pytest

from app.services.media_storage import S3SourceStorage, StorageError
from tests.test_media_sources_l16 import media_db


def settings(key):
    return SimpleNamespace(media_s3_endpoint_url='https://storage.invalid', media_s3_bucket='test',
        media_s3_region='hel1', media_s3_access_key_id='synthetic-access',
        media_s3_secret_access_key='synthetic-secret', media_s3_sse_customer_key=key,
        media_s3_sse_customer_key_id='synthetic-key-id')


def test_encoded_key_is_decoded_once_for_put_get_and_head(monkeypatch, media_db):
    raw = bytes(range(32)); encoded = base64.b64encode(raw).decode()
    client = Mock(); client.get_object.return_value = {'Body': 'handle'}
    client.meta.endpoint_url = 'https://storage.invalid'
    client.create_multipart_upload.return_value = {'UploadId': 'synthetic-upload'}
    client.upload_part.return_value = {'ETag': 'synthetic-etag'}
    client.complete_multipart_upload.return_value = {'VersionId': 'synthetic-version'}
    monkeypatch.setattr('app.database.SessionLocal', media_db[0])
    monkeypatch.setattr(boto3, 'client', lambda *args, **kwargs: client)
    storage = S3SourceStorage(settings('base64:' + encoded))
    storage.put('synthetic/object', b'fixture', content_type='text/plain')
    assert storage.open('synthetic/object') == 'handle'
    assert storage.exists('synthetic/object')
    client.put_object.assert_not_called()
    for method in (client.create_multipart_upload, client.upload_part,
                   client.complete_multipart_upload, client.get_object, client.head_object):
        params = dict(method.call_args.kwargs)
        assert params['SSECustomerKey'] in (raw, encoded)
        _sse_md5(params)
        assert params['SSECustomerKey'] == encoded
        assert len(base64.b64decode(params['SSECustomerKey'])) == 32
        assert params['SSECustomerAlgorithm'] == 'AES256'


@pytest.mark.parametrize('key', ['base64:PRIVATE_INVALID!', 'base64:',
    'base64:' + base64.b64encode(b'x' * 31).decode(),
    'base64:' + base64.b64encode(b'x' * 33).decode()])
def test_bad_encoded_keys_fail_without_disclosing_material(key, monkeypatch):
    factory = Mock(); monkeypatch.setattr(boto3, 'client', factory)
    with pytest.raises(StorageError) as failed:
        S3SourceStorage(settings(key))
    assert failed.value.code == 'storage_encryption_configuration'
    assert key not in str(failed.value) and 'PRIVATE' not in str(failed.value)
    factory.assert_not_called()


def test_existing_unprefixed_configuration_is_preserved(monkeypatch):
    monkeypatch.setattr(boto3, 'client', Mock())
    storage = S3SourceStorage(settings('x' * 32))
    assert storage._sse['SSECustomerKey'] == 'x' * 32
