"""Only synthetic scratch files, always confined to pytest's temporary root."""
import asyncio
from pathlib import Path
import tempfile

import pytest

from app.config import get_settings
from app.services.voice_runtime_cleanup import cleanup_voice_runtime, private_voice_directory, runtime_admission


@pytest.fixture
def runtime_settings(tmp_path, monkeypatch):
    settings=get_settings()
    monkeypatch.setattr(settings,"voice_temp_path",str(tmp_path/"private-voice"))
    return settings


def test_scratch_cleanup_waits_for_active_writer_and_preserves_unrelated_files(runtime_settings):
    root=Path(runtime_settings.voice_temp_path)
    with private_voice_directory("prepare-",settings=runtime_settings) as directory:
        (directory/"source.media").write_bytes(b"synthetic")
        assert cleanup_voice_runtime(runtime_settings) is None
        assert (directory/"source.media").exists()
    (root/"unrelated.txt").write_text("keep",encoding="utf-8")
    orphan=root/"prepare-synthetic-crash";orphan.mkdir();(orphan/"source.media").write_bytes(b"synthetic")
    assert cleanup_voice_runtime(runtime_settings)==1
    assert not orphan.exists() and (root/"unrelated.txt").read_text()=="keep"


def test_late_worker_is_rechecked_before_creating_any_private_file(runtime_settings):
    def stale():
        raise RuntimeError("synthetic stale admission")
    with runtime_admission(stale), pytest.raises(RuntimeError,match="stale admission"):
        with private_voice_directory("synthesis-",settings=runtime_settings):
            pytest.fail("stale worker entered private writer")
    assert list(Path(runtime_settings.voice_temp_path).glob("synthesis-*"))==[]


def test_library_temp_and_copied_thread_context_are_scoped_and_erased(runtime_settings):
    checks=[];original=tempfile.tempdir
    def work():
        with private_voice_directory("synthesis-",settings=runtime_settings,redirect_library_temp=True) as directory:
            with tempfile.NamedTemporaryFile(delete=False) as handle:
                handle.write(b"synthetic-reference")
                assert Path(handle.name).parent==directory
            return directory
    async def run():
        with runtime_admission(lambda:checks.append("fresh")):
            return await asyncio.to_thread(work)
    directory=asyncio.run(run())
    assert checks==["fresh"] and not directory.exists() and tempfile.tempdir==original


def test_cleanup_uncertainty_never_reports_absence(runtime_settings,monkeypatch):
    import app.services.voice_runtime_cleanup as service
    root=Path(runtime_settings.voice_temp_path);root.mkdir()
    orphan=root/"synthesis-synthetic-crash";orphan.mkdir()
    monkeypatch.setattr(service.shutil,"rmtree",lambda path:None)
    assert cleanup_voice_runtime(runtime_settings) is None


def test_production_wrong_host_or_missing_shared_root_fails_closed(runtime_settings, monkeypatch):
    import app.services.voice_runtime_cleanup as service
    monkeypatch.setattr(runtime_settings, "legarya_debug", False)
    monkeypatch.setattr(runtime_settings, "voice_runtime_host_id", "a" * 32)
    monkeypatch.setattr(service, "_machine_id", lambda: "b" * 32)
    assert cleanup_voice_runtime(runtime_settings) is None
    with pytest.raises(OSError, match="topology"):
        with private_voice_directory("prepare-", settings=runtime_settings):
            pytest.fail("wrong-host writer admitted")
    monkeypatch.setattr(service, "_machine_id", lambda: "a" * 32)
    assert cleanup_voice_runtime(runtime_settings) is None
    Path(runtime_settings.voice_temp_path).mkdir()
    monkeypatch.setattr(runtime_settings, "voice_runtime_root_id", "0:0")
    with pytest.raises(OSError, match="identity"):
        with private_voice_directory("prepare-", settings=runtime_settings):
            pytest.fail("different underlying scratch volume admitted")
