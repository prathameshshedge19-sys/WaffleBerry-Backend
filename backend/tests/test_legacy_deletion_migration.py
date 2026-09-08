import sqlalchemy as sa
import pytest
from tests.test_personality_migration_l13 import alembic

def test_deletion_marker_upgrade_downgrade_reupgrade_preserves_existing_legacy(tmp_path):
    path=tmp_path/'delete-migration.db';alembic(path,'upgrade','0021_visual_companions')
    engine=sa.create_engine('sqlite:///'+path.as_posix())
    with engine.begin() as db:
        db.exec_driver_sql("INSERT INTO users(id,full_name,email,password_hash) VALUES(1,'Synthetic owner','qa@example.com','not-credentials')")
        db.exec_driver_sql("INSERT INTO legacies(id,owner_user_id,subject_name,setup_status) VALUES(1,1,'Synthetic Legacy','active')")
    alembic(path,'upgrade','head')
    with engine.connect() as db:
        assert db.exec_driver_sql('SELECT subject_name,deletion_requested_at FROM legacies').one()==('Synthetic Legacy',None)
    alembic(path,'downgrade','0021_visual_companions');alembic(path,'upgrade','head')
    with engine.connect() as db:
        assert db.exec_driver_sql('SELECT subject_name,deletion_requested_at FROM legacies').one()==('Synthetic Legacy',None)
    engine.dispose()

def test_downgrade_cannot_forget_pending_erasure(tmp_path):
    path=tmp_path/'pending-delete.db';alembic(path,'upgrade','head')
    engine=sa.create_engine('sqlite:///'+path.as_posix())
    with engine.begin() as db:
        db.exec_driver_sql("INSERT INTO users(id,full_name,email,password_hash) VALUES(1,'Synthetic owner','qa@example.com','not-credentials')")
        db.exec_driver_sql("INSERT INTO legacies(id,owner_user_id,setup_status,deletion_requested_at) VALUES(1,1,'archived',CURRENT_TIMESTAMP)")
    with pytest.raises(AssertionError,match='Finish pending Legacy erasure'):
        alembic(path,'downgrade','0021_visual_companions')
    with engine.connect() as db:
        assert db.exec_driver_sql('SELECT deletion_requested_at FROM legacies').scalar() is not None
        assert db.exec_driver_sql('SELECT version_num FROM alembic_version').scalar()=='0022_legacy_deletion'
    engine.dispose()
