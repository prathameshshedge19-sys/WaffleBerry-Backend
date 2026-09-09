import io

import pytest
from PIL import Image
from sqlalchemy import select

from app.models.legacy import Legacy
from app.models.media_source import MediaSource
from app.models.viewer import LegacyViewerAccess
from app.services import visual_reference
from app.services.media_sources import MediaSourceService
from app.services.visual_storage import VisualStorage
from tests.test_visual_api_l19 import api, private  # noqa: F401
from tests.test_visual_companions_l19 import visual  # noqa: F401
from tests.visual_l19_helpers import source, factual_snapshot

BASE = '/api/v1/legacies/1/visual-companion/display-picture'


@pytest.fixture(autouse=True)
def decoder(monkeypatch):
    monkeypatch.setattr(visual_reference, 'normalize_crop', visual_reference._decode_image)


@pytest.mark.parametrize('actor,status', [(1,200),(2,404),(3,200),(4,404),(None,401)])
def test_private_full_original_compatibility(api, actor, status):
    response = api.request('GET', BASE, actor)
    private(response, status)
    if status == 200:
        picture = api.request('GET', '/api/v1' + response.json()['content_path'], actor)
        private(picture, 200)
        assert Image.open(io.BytesIO(picture.content)).size == (256,256)
        assert picture.headers['content-type'] == 'image/jpeg'


def test_select_no_face_no_identity_no_factual_effects_idempotent_and_replace(api):
    with api.factory() as db:
        db.get(Legacy,1).setup_status='collecting_identity'
        db.commit()
        first=source(db,api.local); second=source(db,api.local)
        before=factual_snapshot(db)
    response=api.request('PUT',BASE,json={'source_id':first}); private(response,200)
    assert api.request('PUT',BASE,json={'source_id':first}).json()==response.json()
    old_path=response.json()['content_path']
    private(api.request('PUT',BASE,json={'source_id':second}),200)
    private(api.request('GET','/api/v1'+old_path,3),404)
    with api.factory() as db:
        assert factual_snapshot(db)==before
        selected=[s for s in db.scalars(select(MediaSource)) if s.metadata_json.get('display_picture',{}).get('selected')]
        assert [s.id for s in selected]==[second]


@pytest.mark.parametrize('actor,status',[(2,404),(3,404),(4,404)])
def test_only_owner_changes_picture(api,actor,status):
    private(api.request('PUT',BASE,actor,json={'source_id':api.source_id}),status)


def test_cross_legacy_and_unready_rejected(api):
    with api.factory() as db:
        other=source(db,api.local,2)
        unready=source(db,api.local)
        db.get(MediaSource,unready).safety_state='pending';db.commit()
    for ident in (other,unready):
        private(api.request('PUT',BASE,json={'source_id':ident}),409)


def test_deletion_does_not_resurrect_previous_approved_picture(api):
    with api.factory() as db:
        ident=source(db,api.local)
    private(api.request('PUT',BASE,json={'source_id':ident}),200)
    with api.factory() as db:
        from app.models.user import User
        MediaSourceService(storage=api.local).delete(db,db.get(User,1),1,ident)
    assert api.request('GET',BASE,3).json()['available'] is False
    with api.factory.begin() as db:
        row=db.get(MediaSource,ident);row.state='deleted';row.metadata_json={}
    assert api.request('GET',BASE,3).json()['available'] is False


@pytest.mark.parametrize('action',['revoke','delete','replace'])
def test_reauthorize_after_storage_read(api,monkeypatch,action):
    info=api.request('GET',BASE,3).json()
    original=VisualStorage.read_original
    def raced(self,*args):
        result=original(self,*args)
        with api.factory() as db:
            if action=='revoke':db.scalar(select(LegacyViewerAccess).where(LegacyViewerAccess.user_id==3)).status='revoked'
            elif action=='delete':db.get(MediaSource,api.source_id).state='deleting'
            else:
                from app.services.display_picture import select_picture
                ident=source(db,api.local)
                select_picture(db,1,1,ident)
            db.commit()
        return result
    monkeypatch.setattr(VisualStorage,'read_original',raced)
    private(api.request('GET','/api/v1'+info['content_path'],3),404)


def test_display_decode_preserves_entire_landscape_no_face_and_strips_metadata():
    picture=Image.new('RGB',(900,400),'green');picture.paste('red',(0,0,60,400));picture.paste('blue',(840,0,900,400))
    data=io.BytesIO();picture.save(data,'PNG')
    result=Image.open(io.BytesIO(visual_reference._decode_image(data.getvalue(),{'display_picture':True})))
    assert result.size==(900,400)
    assert result.getpixel((10,200))[0]>200 and result.getpixel((890,200))[2]>200
    assert not result.getexif()
