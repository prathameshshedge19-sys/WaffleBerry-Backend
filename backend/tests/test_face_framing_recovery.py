import io
import json
import pytest
from PIL import Image
from app.services.visual_native import build_rig
from app.services.visual_reference import _decode_image
from app.services.visual_provider import validate_bundle

def test_small_single_face_input_uses_detection_canvas_not_tiny_letterbox():
    image=Image.new('RGB',(192,256),(140,30,60));raw=io.BytesIO();image.save(raw,format='PNG')
    with Image.open(io.BytesIO(_decode_image(raw.getvalue(),dict(x=0,y=0,width=1,height=1,rotation=0,auto_fit=True)))) as normalized:
        assert normalized.getpixel((512,50))==(140,30,60)
        assert normalized.getpixel((512,970))==(140,30,60)

@pytest.mark.parametrize('offset',[i*.002 for i in range(24)])
def test_usable_face_does_not_require_user_to_hit_arbitrary_mesh_rows(offset):
    points=[[.5,.5] for _ in range(478)]
    points[10]=[.5,.2];points[152]=[.5,.8]
    for indices,cx,cy,width in [((33,133,159,145),.35,.34+offset,.085),((362,263,386,374),.65,.34+offset,.085),((61,291,13,14),.5,.65+offset,.16)]:
        for i,point in zip(indices,[(cx-width/2,cy),(cx+width/2,cy),(cx,cy-.01),(cx,cy+.01)]):points[i]=list(point)
    raw=io.BytesIO();Image.new('RGB',(512,512),'white').save(raw,format='PNG')
    bundle=build_rig(raw.getvalue(),points,'a'*64);validate_bundle(bundle)
    rig=json.loads(bundle.assets['rig'])
    assert len(rig['vertices'])<=512 and len(rig['triangles'])<=1024
    assert all(max(map(abs,v))>=.0002 for v in rig['deformations'].values())
