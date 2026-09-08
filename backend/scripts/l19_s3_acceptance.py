"""Explicit synthetic-only live S3 acceptance; never enumerate other prefixes.

Run only with operator approval. Credentials remain in memory; registry/evidence
contain only this generated QA namespace. No application database is opened.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.media_storage import S3SourceStorage, StorageError
from app.services.visual_storage import VisualStorage, _S3Operations


def late_writer(connection, client_args, bucket, sse, key, data):
    import boto3
    client = boto3.client('s3', **client_args)
    try:
        assert connection.recv() == 'release'
        client.put_object(Bucket=bucket, Key=key, Body=data, IfNoneMatch='*', **sse)
        connection.send('committed')  # QA barrier, deliberately NOT a SQL receipt.
        connection.recv()  # Simulate loss of the application result channel.
    finally:
        client.close()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--env-file', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--allow-live-synthetic-prefix', action='store_true', required=True)
    args=parser.parse_args()
    from dotenv import dotenv_values
    from botocore.config import Config
    values=dotenv_values(args.env_file)
    names=('endpoint_url','bucket','region','access_key_id','secret_access_key',
           'sse_customer_key','sse_customer_key_id')
    settings=SimpleNamespace(**{'media_s3_'+key:values.get('MEDIA_S3_'+key.upper()) for key in names})
    del values
    if not settings.media_s3_endpoint_url or not settings.media_s3_endpoint_url.startswith('https://'):
        raise SystemExit('HTTPS S3 QA configuration required')
    source=S3SourceStorage(settings)
    storage=VisualStorage(source)
    # Independent no-retry client for source-sized PUT/negative auth QA only.
    client_args=dict(endpoint_url=settings.media_s3_endpoint_url,region_name=settings.media_s3_region,
        aws_access_key_id=settings.media_s3_access_key_id,aws_secret_access_key=settings.media_s3_secret_access_key,
        config=Config(connect_timeout=3,read_timeout=10,retries={'total_max_attempts':1}))
    import boto3
    client=boto3.client('s3',**client_args)
    prefix='qa/l19-phase-b/'+str(uuid4())+'/'
    output=Path(args.output).resolve()
    output.mkdir(mode=0o700,parents=True,exist_ok=False)  # Never overwrite a prior uncertain registry.
    registry={}
    checks={}
    error=None

    def persist():
        temporary=output/'registry.tmp'
        with temporary.open('w') as stream:
            stream.write(json.dumps({'prefix':prefix,'objects':registry},indent=2))
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(output/'registry.json')

    def reserve(name,data):
        key=prefix+name
        assert key.startswith(prefix) and key not in registry and '/' not in name
        registry[key]={'sha256':hashlib.sha256(data).hexdigest(),'size':len(data),'version':None,'state':'reserved'}
        persist()  # Exact key BEFORE every first mutation.
        return key

    def put(key,data,mime='application/octet-stream'):
        assert key in registry and key.startswith(prefix)
        registry[key]['state']='dispatching'; persist()
        result=storage.put(key,data,mime)
        registry[key].update(version=result.version,state='confirmed'); persist()
        return result

    process=None
    try:
        import io
        from PIL import Image
        pixels=io.BytesIO()
        Image.new('RGB',(64,64),(40,90,140)).save(pixels,format='PNG')
        for role,mime in [('poster','image/png'),('texture_atlas','image/png'),('rig','application/json')]:
            data=b'{"synthetic_qa":true}' if role=='rig' else pixels.getvalue()
            key=reserve(role,data); result=put(key,data,mime)
            checks[role+'_put']=True
            assert storage.read(key,result.version)==data
            assert storage.verify(key,registry[key]['sha256'],len(data),result.version)
        checks.update(exact_read=True,byte_size=True,checksum=True,correct_sse_c=True)
        sample=prefix+'poster'
        for name,sse in [('no_key',{}),('wrong_key',{'SSECustomerAlgorithm':'AES256','SSECustomerKey':b'w'*32})]:
            try:
                response=client.get_object(Bucket=source.bucket,Key=sample,**sse)
                response['Body'].close()
                raise AssertionError('SSE-C access unexpectedly succeeded')
            except AssertionError:
                raise
            except Exception as exc:
                status=getattr(exc,'response',{}).get('ResponseMetadata',{}).get('HTTPStatusCode')
                assert status in (400,403), 'Negative key result was not an authorization/encryption denial'
                checks[name+'_denied']=True
        data=b'L19 synthetic bounded original\n'*(20*1024*1024//31+1)
        data=(data+b'\0'*(20*1024*1024))[:20*1024*1024]
        key=reserve('original-20m',data)
        registry[key]['state']='dispatching'; persist()
        result=client.put_object(Bucket=source.bucket,Key=key,Body=data,IfNoneMatch='*',**source._sse)
        registry[key].update(version=result.get('VersionId'),state='confirmed'); persist()
        actual=storage.read_original(key,registry[key]['version'])
        assert len(actual)==len(data) and hashlib.sha256(actual).hexdigest()==registry[key]['sha256']
        checks['bounded_source_read']=True
        try:
            storage.read(key,registry[key]['version'])
            raise AssertionError('Generated reader accepted source-sized data')
        except StorageError as exc:
            assert exc.code=='storage_size_exceeded'
        checks['generated_reader_stays_bounded']=True
        del actual,data
        late=b'fictional late generated object'
        key=reserve('late-uncertain',late)
        assert storage.reconcile_write(key,registry[key]['sha256'],len(late)) is False
        checks['initial_absence_is_not_completion']=True
        registry[key]['state']='dispatching'; persist()
        context=multiprocessing.get_context('spawn')
        parent,child=context.Pipe()
        process=context.Process(target=late_writer,args=(child,client_args,source.bucket,source._sse,key,late))
        process.start(); child.close()
        parent.send('release')
        assert parent.poll(30) and parent.recv()=='committed'
        process.terminate(); process.join(2)
        assert not process.is_alive()
        parent.close()
        checks['interrupted_result_channel']=True
        assert storage.reconcile_write(key,registry[key]['sha256'],len(late)) is True
        registry[key]['state']='confirmed_by_exact_bytes'; persist()
        checks['late_committed_object_discovered']=True
        storage.erase(key)
        assert not storage.reconcile_write(key,registry[key]['sha256'],len(late))
        checks['late_object_removed']=True
        # If this bucket has versioning, exercise an actual marker on OUR key.
        result=client.get_bucket_versioning(Bucket=source.bucket)
        versioning=result.get('Status')=='Enabled'
        checks['versioning_enabled']=versioning
        marker_key=reserve('marker-check',b'synthetic marker payload')
        put(marker_key,b'synthetic marker payload')
        if versioning:
            marker=client.delete_object(Bucket=source.bucket,Key=marker_key)
            registry[marker_key]['delete_marker_version']=marker.get('VersionId'); persist()
            checks['delete_marker_created']=True
        storage.erase(marker_key)
        checks['exact_version_delete']=True
    except Exception as exc:
        error=getattr(exc,'code',type(exc).__name__)  # No raw SDK details/secrets.
    finally:
        if process is not None and process.is_alive():
            process.kill(); process.join(2)
        cleanup=True
        for key in registry:
            assert key.startswith(prefix)
            try:
                storage.erase(key)
                registry[key]['cleanup']='absent_observed'
            except Exception:
                cleanup=False
                registry[key]['cleanup']='pending'
        persist()
        # All successful PUTs now have positive completion evidence. Observe a
        # second absent sweep after the production stability interval; elapsed
        # time alone would never resolve an unacknowledged/unknown request.
        first_absent=time.monotonic()
        while time.monotonic()-first_absent < 60:
            time.sleep(max(0,min(1,60-(time.monotonic()-first_absent))))
        # Repeated exact-version reconciliation; NEVER a bucket-wide listing.
        for key in registry:
            try:
                storage.erase(key)
            except Exception:
                cleanup=False
        zero=False
        try:
            page=client.list_objects_v2(Bucket=source.bucket,Prefix=prefix,MaxKeys=100)
            versions=client.list_object_versions(Bucket=source.bucket,Prefix=prefix,MaxKeys=100)
            zero=(not page.get('Contents') and not page.get('IsTruncated') and
                  not versions.get('Versions') and not versions.get('DeleteMarkers') and not versions.get('IsTruncated'))
        except Exception:
            cleanup=False
        unknown=any(row['state']=='dispatching' for row in registry.values())
        checks['repeated_reconciliation']=cleanup
        checks['final_zero_objects_and_versions']=bool(zero and cleanup and not unknown)
        result={'prefix':prefix,'checks':checks,'error':error,'registry_count':len(registry),
                'accepted':error is None and zero and cleanup and not unknown}
        (output/'result.json').write_text(json.dumps(result,indent=2))
        client.close(); source.client.close()
    print(json.dumps(result))
    return 0 if result['accepted'] else 1


if __name__=='__main__':
    raise SystemExit(main())
