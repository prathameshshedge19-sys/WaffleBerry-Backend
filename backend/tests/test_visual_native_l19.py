"""Real native acceptance is opt-in, synthetic-only and separate from fake tests."""
import hashlib
import io
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import time
from datetime import timedelta
from uuid import uuid4

import pytest
from PIL import Image
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.database import Base, build_engine
from app.models.media_source import MediaSource
from app.models.user import User
from app.models.viewer import LegacyViewerAccess
from app.models.visual_companion import VisualCompanion, VisualCompanionVersion, VisualCompanionAsset, VisualGenerationJob
from app.services import visual_local_provider as local
from app.services.visual_provider import VisualBundleError, validate_bundle
from app.services.media_storage import LocalSourceStorage
from app.services.media_sources import MediaSourceService
from app.services.media_worker import MediaIntelligenceWorker
from app.services.visual_worker import VisualWorker
from app.services.visual_companions import VisualCompanionService, manifest
from app.services.visual_companions import utcnow
from app.services.visual_storage import VisualStorage
from tests.visual_l19_helpers import seed, command, approval, factual_snapshot


@pytest.fixture
def native():
    names=('L19_NATIVE_PYTHON','L19_NATIVE_MODEL','L19_SYNTHETIC_SHEET')
    if sys.platform!='linux' or not all(os.environ.get(n) for n in names):
        pytest.skip('Requires explicit isolated Linux native runtime and synthetic portrait fixture')
    return local.LocalPortraitRigProvider(os.environ[names[0]],os.environ[names[1]],
        library_dir=os.environ.get('L19_NATIVE_LIBRARY_DIR'))


def portrait(category='frontal'):
    with Image.open(os.environ['L19_SYNTHETIC_SHEET']) as sheet:
        w,h=sheet.size
        boxes={'frontal':(0,0,w//2,h//2),'grayscale':(w//2,0,w,h//2),
               'glasses':(0,h//2,w//2,h),'group':(w//2,h//2,w,h)}
        key='group' if category.startswith('group') else category if category in boxes else 'frontal'
        image=sheet.crop(boxes[key]).convert('RGB')
        if category=='low_resolution':
            image=image.resize((192,192))
        output=io.BytesIO(); image.save(output,format='PNG')
    crop=dict(x=0,y=0,width=1,height=1,rotation=0)
    if category=='group_selected':
        crop=dict(x=.03,y=.05,width=.5,height=.5,rotation=0)
    if category=='difficult_crop':
        crop=dict(x=0,y=0,width=.3,height=.3,rotation=0)
    return output.getvalue(),crop


@pytest.mark.parametrize('category',['frontal','grayscale','glasses','low_resolution','group_selected'])
def test_real_portrait_bundle_and_resource_bounds(native,category):
    data,crop=portrait(category)
    start=time.monotonic()
    bundle=native.prepare(data,crop,{'source_sha256':hashlib.sha256(data).hexdigest()})
    descriptors=validate_bundle(bundle)  # All 27 endpoint/intermediate combinations.
    rig=json.loads(bundle.assets['rig'])
    assert rig['fake_only'] is False
    assert len(rig['vertices'])<=512 and len(rig['triangles'])<=1024
    assert sum(len(x) for x in bundle.assets.values())<=2*1024*1024
    assert native.last_metrics['peak_rss_kib'] < 768*1024
    assert time.monotonic()-start<120
    assert {d['logical_role'] for d in descriptors}=={'poster','texture_atlas','rig'}
    assert all(any(abs(v)>.0002 for v in channel) for channel in rig['deformations'].values())


@pytest.mark.parametrize('category',['group_ambiguous','difficult_crop'])
def test_real_provider_rejects_ambiguous_or_unusable_crop(native,category):
    data,crop=portrait(category)
    with pytest.raises(VisualBundleError,match='visual_needs_recrop'):
        native.prepare(data,crop,{})


def test_real_provider_repeat_is_deterministic(native):
    data,crop=portrait()
    first=native.prepare(data,crop,{})
    second=native.prepare(data,crop,{})
    assert first.assets==second.assets


@pytest.mark.parametrize('layout',['center','off_center','tall','small','distant','manual_distant','ambiguous'])
def test_real_automatic_framing_uses_entire_photo_and_rejects_multiple_faces(native,layout):
    data,_=portrait('group_ambiguous' if layout=='ambiguous' else 'frontal')
    if layout=='small':data,_=portrait('low_resolution')
    if layout in {'distant','manual_distant'}:
        with Image.open(io.BytesIO(data)) as face:
            scene=Image.new('RGB',(1600,1600),(220,225,230));scene.paste(face.resize((256,256)),(1150,700))
            output=io.BytesIO();scene.save(output,format='PNG');data=output.getvalue()
    if layout in {'off_center','tall'}:
        with Image.open(io.BytesIO(data)) as face:
            scene=Image.new('RGB',(1024,1024) if layout=='off_center' else (700,1400),(230,237,225))
            scene.paste(face.resize((384,384)),(600,25) if layout=='off_center' else (140,30))
            encoded=io.BytesIO();scene.save(encoded,format='PNG');data=encoded.getvalue()
    crop=dict(x=0,y=0,width=1,height=1,rotation=0,auto_fit=True)
    if layout=='manual_distant':crop['auto_fit']=False
    if layout=='ambiguous':
        with pytest.raises(VisualBundleError,match='visual_needs_recrop'):native.prepare(data,crop,{})
        return
    bundle=native.prepare(data,crop,{})
    validate_bundle(bundle)
    assert json.loads(bundle.assets['rig'])['fake_only'] is False
    assert native.last_metrics['peak_rss_kib']<768*1024
    if layout=='off_center':assert native.prepare(data,crop,{}).assets==bundle.assets


def test_native_model_pin_rejects_wrong_bytes(tmp_path):
    bad=tmp_path/'wrong.task'; bad.write_bytes(b'not the model')
    with pytest.raises(VisualBundleError,match='visual_model_mismatch'):
        local.LocalPortraitRigProvider(sys.executable,bad)


def test_default_configuration_never_selects_fake(monkeypatch):
    from app.config import get_settings
    monkeypatch.setenv('VISUAL_PREPARATION_ENABLED','false')
    get_settings.cache_clear()
    try:
        with pytest.raises(VisualBundleError,match='visual_provider_unavailable'):
            local.configured_provider()
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize('raw', [b'private parser content', b'null', b'[]', b'{}',
    b'{"assets":[]}', b'{"assets":{"poster":"%%%"}}', b'{"error":[]}',
    b'{"error":"private native diagnostics"}', b'\xff'])
def test_native_output_errors_are_fixed_and_sanitized(raw):
    with pytest.raises(VisualBundleError) as caught:
        local._decode_output(raw, '0'*64)
    assert str(caught.value) == 'visual_native_failed'
    assert caught.value.__cause__ is None


def test_native_output_is_bounded_before_json_parse():
    with pytest.raises(VisualBundleError, match='visual_bundle_too_large'):
        local._decode_output(b'x'*(local.MAX_OUTPUT+1), '0'*64)


def test_native_recrop_error_survives_sanitization():
    with pytest.raises(VisualBundleError, match='visual_needs_recrop'):
        local._decode_output(b'{"error":"visual_needs_recrop"}', '0'*64)


@pytest.mark.parametrize('mode',['deadline','cancel','crash','invalid','memory'])
def test_native_supervisor_failure_cleanup(native,monkeypatch,tmp_path,mode):
    data,crop=portrait()
    original=subprocess.Popen
    captured=[]
    scripts={'deadline':'import time; time.sleep(10)', 'cancel':'import time; time.sleep(10)',
             'crash':'import os; os._exit(7)', 'invalid':'print("invalid JSON")',
             'memory':'import resource; resource.setrlimit(resource.RLIMIT_AS,(768*1024*1024,)*2); bytearray(800*1024*1024)'}
    def launch(args,**kwargs):
        if len(args)>3 and str(args[3]).endswith('visual_native.py'):
            args=[native.python,'-I','-c',scripts[mode]]
            captured.append(kwargs['cwd'])
        return original(args,**kwargs)
    monkeypatch.setattr(local.subprocess,'Popen',launch)
    start=time.monotonic()
    with pytest.raises(VisualBundleError):
        native.prepare(data,crop,{},deadline=start+2 if mode=='deadline' else start+15,
            cancelled=(lambda: bool(captured) and time.monotonic()-start>.7) if mode=='cancel' else None)
    assert time.monotonic()-start<8
    assert captured and all(not Path(p).exists() for p in captured)


def test_native_seccomp_and_landlock_deny_network_and_private_files(native,tmp_path):
    # Execute confinement itself, without the outer unit being the only proof.
    secret=tmp_path/'unrelated-private-sentinel'; secret.write_text('synthetic sentinel')
    native_module=Path(local.__file__).with_name('visual_native.py')
    work=tmp_path/'l19-native-confinement'; work.mkdir(mode=0o700)
    script=f'''import importlib.util,os,socket,tempfile,json
s=importlib.util.spec_from_file_location("isolated",{str(native_module)!r}); m=importlib.util.module_from_spec(s); s.loader.exec_module(m)
os.chdir({str(work)!r})
m.confine([{native.library_dir!r}])
checks=[]
for family in (socket.AF_INET,socket.AF_INET6,socket.AF_UNIX):
 try: socket.socket(family,socket.SOCK_STREAM); checks.append(False)
 except OSError: checks.append(True)
try: open({str(secret)!r}).read(); checks.append(False)
except PermissionError: checks.append(True)
try: socket.getaddrinfo("l19-network-probe.invalid",443); checks.append(False)
except OSError: checks.append(True)
print(json.dumps(checks))
'''
    result=subprocess.run([native.python,'-I','-c',script],capture_output=True,timeout=15,
        env={'PATH':'/usr/bin:/bin'},check=True)
    assert json.loads(result.stdout)==[True]*5


@pytest.mark.parametrize('fault', [None, 'lease_loss', 'source_delete'])
def test_real_lifecycle_zero_effects_and_source_erasure(native,tmp_path,monkeypatch,fault):
    engine=build_engine('sqlite:///'+str(tmp_path/'native-lifecycle.db'))
    Base.metadata.create_all(engine)
    sessions=sessionmaker(bind=engine,expire_on_commit=False,autoflush=False)
    source_store=LocalSourceStorage(str(tmp_path/'sources'))
    derivatives=VisualStorage(LocalSourceStorage(str(tmp_path/'derivatives')))
    media=MediaSourceService(storage=source_store)
    service=VisualCompanionService(provider_name=native.provider_name,model_digest=native.model_digest)
    worker=VisualWorker(sessions,derivatives,provider=native,source_storage=source_store)
    data,crop=portrait()
    try:
        with sessions() as db:
            seed(db)
            db.add(LegacyViewerAccess(legacy_id=1,user_id=3,status='active')); db.commit()
            before=factual_snapshot(db)
            source=media.create(db,db.get(User,1),1,kind='image',filename='fictional-qa.png',
                mime_type='image/png',size_bytes=len(data),upload_request_key=str(uuid4()),processing_purpose='visual_reference')
            source_id=source.id
            media.receive(db,db.get(User,1),1,source_id,data)
        validator=MediaIntelligenceWorker(sessions=sessions,storage=source_store)
        try:
            assert validator.run_once()=='visual_reference_validated'
        finally:
            validator.close()
        with sessions() as db:
            version=service.admit(db,1,1,command(source_id,crop=crop)); db.commit()
            version_id=version.id
            assert factual_snapshot(db)==before
        if fault:
            original = subprocess.Popen
            children = []
            def launch(args, **kwargs):
                process = original(args, **kwargs)
                if len(args)>3 and str(args[3]).endswith('visual_native.py'):
                    children.append((process, kwargs['cwd']))
                    with sessions() as db:
                        if fault == 'source_delete':
                            media.delete(db, db.get(User,1), 1, source_id)
                        else:
                            job = db.scalar(select(VisualGenerationJob).where(
                                VisualGenerationJob.version_id==version_id,
                                VisualGenerationJob.kind=='prepare'))
                            job.lease_token = str(uuid4())
                        db.commit()
                return process
            monkeypatch.setattr(local.subprocess, 'Popen', launch)
            assert worker.run_once() == 'stale'
            assert len(children) == 1
            assert children[0][0].poll() is not None and not Path(children[0][1]).exists()
            with sessions() as db:
                assert db.get(VisualCompanionVersion,version_id).state != 'ready'
                assert not list(db.scalars(select(VisualCompanionAsset)))
                assert not db.scalar(select(VisualCompanion)).enabled
                assert factual_snapshot(db)==before
            return
        assert worker.run_once()=='ready'
        with sessions() as db:
            version=db.get(VisualCompanionVersion,version_id)
            profile=db.scalar(select(VisualCompanion))
            assert not profile.enabled
            assert manifest(db,1,1,version_id=version_id)
            service.activate(db,1,1,approval(version,profile.revision)); db.commit()
            assert manifest(db,3,1,viewer=True)
            service.toggle(db,1,1,False,profile.revision); db.commit()
            with pytest.raises(HTTPException): manifest(db,3,1,viewer=True)
            service.toggle(db,1,1,True,profile.revision); db.commit()
            assert factual_snapshot(db)==before
            keys=list(db.scalars(select(VisualCompanionAsset.object_key)))
            media.delete(db,db.get(User,1),1,source_id)
            with pytest.raises(HTTPException): manifest(db,3,1,viewer=True)
            assert factual_snapshot(db)==before
        worker.clock=lambda: utcnow()+timedelta(seconds=121)
        assert worker.run_once()=='purged'
        assert all(not derivatives.storage.exists(key) for key in keys)
        with sessions() as db:
            assert factual_snapshot(db)==before
    finally:
        engine.dispose()


def test_native_startup_sweep_skips_live_and_unregistered_paths(native, tmp_path, monkeypatch):
    monkeypatch.setattr(local.tempfile, 'gettempdir', lambda: str(tmp_path))
    with local.native_workspace() as (work, lease):
        assert local.sweep_native_workspaces() == 0
        assert Path(work).exists()
    root = local._workspace_root()
    unregistered = root/'l19-native-unregistered'
    unregistered.mkdir(mode=0o700)
    (unregistered/'sentinel').write_text('unregistered synthetic file')
    orphan = root/'l19-native-orphan'
    orphan.mkdir(mode=0o700)
    (orphan/'.lease').write_bytes(local.WORKSPACE_MARKER)
    (orphan/'cache').write_bytes(b'synthetic private cache')
    assert local.sweep_native_workspaces() == 1
    assert not orphan.exists() and (unregistered/'sentinel').exists()


def test_native_parent_death_kills_child_and_startup_reaps_orphan(native,tmp_path,monkeypatch):
    data,crop = portrait()
    input_path = tmp_path/'synthetic-input.png'; input_path.write_bytes(data)
    app_root = str(Path(local.__file__).resolve().parents[2])
    code = f'''import sys,os,json
sys.path.insert(0,{app_root!r})
from app.services import visual_local_provider as m
m.tempfile.tempdir={str(tmp_path)!r}
original=m.subprocess.Popen
def launch(args,**kwargs):
 p=original(args,**kwargs)
 if len(args)>3 and str(args[3]).endswith('visual_native.py'):
  print(json.dumps({{'pid':p.pid,'root':kwargs['cwd']}}),flush=True)
 return p
m.subprocess.Popen=launch
p=m.LocalPortraitRigProvider({native.python!r},{str(native.model)!r},library_dir={native.library_dir!r})
p.prepare(open({str(input_path)!r},'rb').read(),{crop!r},{{}})
'''
    parent = subprocess.Popen([native.python,'-I','-c',code], stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, env={'PATH':'/usr/bin:/bin'}, text=True)
    child = None
    try:
        import select as ready
        assert ready.select([parent.stdout],[],[],15)[0]
        child = json.loads(parent.stdout.readline())
        # Wait for actual native library mapping: parent-death setup precedes it.
        limit = time.monotonic()+10
        while time.monotonic()<limit:
            maps = Path(f"/proc/{child['pid']}/maps")
            if maps.exists() and 'mediapipe' in maps.read_text():
                break
            assert parent.poll() is None
            time.sleep(.01)
        else:
            pytest.fail('Native child did not reach model runtime')
        parent.kill(); parent.wait(timeout=3)
        limit = time.monotonic()+5
        while time.monotonic()<limit:
            status = Path(f"/proc/{child['pid']}/status")
            if not status.exists() or '\nState:\tZ' in status.read_text():
                break
            time.sleep(.01)
        else:
            pytest.fail('Native child survived parent death')
        monkeypatch.setattr(local.tempfile, 'gettempdir', lambda: str(tmp_path))
        assert Path(child['root']).exists()  # Parent could not run its finally.
        assert local.sweep_native_workspaces() == 1
        assert not Path(child['root']).exists()
    finally:
        if parent.poll() is None:
            parent.kill(); parent.wait(timeout=3)
        parent.stdout.close()
