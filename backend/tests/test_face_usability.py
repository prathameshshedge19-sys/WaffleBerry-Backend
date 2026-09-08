import io
import pytest
from PIL import Image
from app.models.legacy import Legacy
from app.services.visual_reference import _decode_image, VisualReferenceError
from app.services.visual_native import auto_frame
from tests.test_visual_api_l19 import api, visual, private, BASE
from tests.test_realtime_l15 import live, create, authenticate, connected


def test_approved_face_is_delivered_to_authorized_draft_viewer(api):
    with api.factory.begin() as db:
        legacy=db.get(Legacy,1); legacy.setup_status='collecting_identity'; legacy.subject_name=None
    response=api.request('GET',BASE+'/active-manifest',3)
    private(response,200)
    for asset in response.json()['assets']:
        private(api.request('GET',asset['content_path'],3),200)
    private(api.request('GET',BASE+'/active-manifest',4),404)
    private(api.request('GET',BASE+'/active-manifest',2),404)


def test_authorized_draft_viewer_can_connect_voice_without_identity(live):
    with live[1].begin() as db:
        legacy=db.get(Legacy,1);legacy.setup_status='collecting_identity';legacy.subject_name=None
    response=create(live,3,legacy_id=1,mode='legacy')
    assert response.status_code==201
    with authenticate(live[0],response.json()) as ws:
        connected(ws,response.json());ws.send_json({'type':'end_call'})
        assert ws.receive_json()['type']=='ended'
    assert create(live,4,legacy_id=1,mode='legacy').status_code==403
    with live[1]() as db:
        assert db.get(Legacy,1).subject_name is None


def test_auto_detection_keeps_full_source_instead_of_center_crop():
    image=Image.new('RGB',(400,1000),'white')
    image.paste((220,20,20),(0,0,400,150));image.paste((20,20,220),(0,850,400,1000))
    encoded=io.BytesIO();image.save(encoded,format='PNG')
    crop=dict(x=0,y=.3,width=1,height=.4,rotation=0,auto_fit=True)
    with Image.open(io.BytesIO(_decode_image(encoded.getvalue(),crop))) as result:
        assert result.size==(1024,1024)
        assert result.getpixel((500,15))[0]>200
        assert result.getpixel((500,1000))[2]>200
        assert not result.info and not result.getexif()
    with Image.open(io.BytesIO(_decode_image(encoded.getvalue(),dict(crop,auto_fit=False)))) as result:
        assert result.size==(512,512) and result.getpixel((250,10))==(255,255,255)


def test_auto_frame_centers_off_center_face_and_leaves_headroom():
    image=Image.new('RGB',(1024,1024),'white');encoded=io.BytesIO();image.save(encoded,format='PNG')
    points=[[.71+(i%2)*.08,.2+(i%3)*.06] for i in range(468)]
    png,framed=auto_frame(encoded.getvalue(),points)
    assert min(p[1] for p in framed)>.15
    assert max(p[1] for p in framed)<.85
    assert min(p[0] for p in framed)>.15 and max(p[0] for p in framed)<.85
    with Image.open(io.BytesIO(png)) as result: assert result.size==(512,512)
    with pytest.raises(ValueError):auto_frame(encoded.getvalue(),[[float('nan'),.5]]*468)


def test_auto_mode_cannot_smuggle_non_boolean_crop_values():
    image=Image.new('RGB',(128,128));encoded=io.BytesIO();image.save(encoded,format='PNG')
    with pytest.raises(VisualReferenceError):
        _decode_image(encoded.getvalue(),dict(x=0,y=0,width=1,height=1,auto_fit='yes'))
