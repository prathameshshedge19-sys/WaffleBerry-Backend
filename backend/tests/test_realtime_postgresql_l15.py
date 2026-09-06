"""Opt-in concurrency on a separately provisioned disposable PostgreSQL cluster."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import os
import threading
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.database import Base, build_engine
from app.models.conversation import Conversation
from app.models.legacy import Legacy
from app.models.realtime_session import RealtimeSession as Live
from app.models.user import User
from app.schemas.realtime import SessionCreate
from app.services import realtime_sessions as service


@pytest.fixture
def pg():
    url=os.environ.get("L15_TEST_POSTGRES_URL")
    if not url: pytest.skip("Requires explicitly configured disposable L15 PostgreSQL database")
    parsed=make_url(url)
    assert parsed.host in {"localhost","127.0.0.1"} and parsed.database.startswith("l15_test")
    engine=build_engine(url)
    schema="l15_test_"+uuid4().hex
    with engine.begin() as c:
        assert c.execute(text("SELECT current_database()")).scalar_one()==parsed.database
        assert c.execute(text("SELECT version_num FROM public.alembic_version")).scalar_one()=="0016_realtime_sessions"
        c.execute(text('CREATE SCHEMA "'+schema+'"'))
    scoped=engine.execution_options(schema_translate_map={None:schema})
    Base.metadata.create_all(scoped)
    factory=sessionmaker(bind=scoped,expire_on_commit=False,autoflush=False)
    with factory.begin() as db:
        db.add(User(id=1,full_name="Disposable",email="probe@example.com",password_hash="test",is_verified=True))
        db.flush();db.add(Legacy(id=1,owner_user_id=1,setup_status="active",subject_name="Disposable"))
    try: yield factory
    finally:
        with engine.begin() as c: c.execute(text('DROP SCHEMA "'+schema+'" CASCADE'))
        engine.dispose()


def race(factory, function):
    barrier=threading.Barrier(2)
    def worker(index):
        with factory() as db:
            pid=db.execute(text("SELECT pg_backend_pid()")).scalar_one()
            barrier.wait(timeout=10)
            try: return pid,function(db,index)
            except service.RealtimeError as error:
                db.rollback();return pid,error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(worker,i) for i in range(2)]
        result=[f.result(timeout=20) for f in futures]
    assert result[0][0]!=result[1][0]
    return [r[1] for r in result]


def grant(db):
    timestamp=service.now().timestamp()
    return service.authorize(db,1,SessionCreate(legacy_id=1,mode="rya"),"http://localhost:5600",
                             {"iat":timestamp,"exp":timestamp+1800},get_settings())


def test_concurrent_authorization_ticket_and_lease(pg):
    results=race(pg,lambda db,i: grant(db))
    assert sum(isinstance(r,dict) for r in results)==1 and "realtime_conflict" in results
    issued=next(r for r in results if isinstance(r,dict))
    results=race(pg,lambda db,i: service.consume(db,issued["ticket"],"http://localhost:5600",str(i),get_settings()))
    assert sum(isinstance(r,tuple) for r in results)==1 and "realtime_ticket_used" in results
    with pg() as db:
        assert db.scalar(select(func.count()).select_from(Live).where(Live.lease_owner.is_not(None)))==1


def test_concurrent_future_binding(pg):
    with pg() as db:
        issued=grant(db)
        sid,generation,_=service.consume(db,issued["ticket"],"http://localhost:5600","binding",get_settings())
        service.owned(db,sid,"binding",generation,get_settings(),ready=True)
    calls=[]
    def bind(db,index):
        def create(row):
            calls.append(index)
            conversation=Conversation(user_id=1,legacy_id=1,mode="rya",title="Disposable")
            db.add(conversation);return conversation
        result=service.bind_once(db,issued["session_id"],1,create,get_settings(),owner="binding",generation=generation)
        db.commit();return result
    results=race(pg,bind)
    assert results[0]==results[1] and len(calls)==1
    with pg() as db: assert db.scalar(select(func.count()).select_from(Conversation))==1


def test_reconnect_generation_fences_old_owner(pg):
    settings=get_settings()
    with pg() as db:
        issued=grant(db);sid,generation,_=service.consume(db,issued["ticket"],"http://localhost:5600","old",settings)
        service.close_owned(db,sid,"old",generation,"browser_disconnect",settings)
        replacement=service.reconnect(db,sid,1,"http://localhost:5600",settings)
    def operation(db,index):
        if index==0: return service.consume(db,replacement["ticket"],"http://localhost:5600","new",settings)
        service.close_owned(db,sid,"old",generation,"client_end",settings);return "old_closed"
    race(pg,operation)
    with pg() as db:
        row=db.get(Live,sid)
        assert row.lease_owner=="new" and row.connection_generation>generation


@pytest.mark.parametrize("action",["revoke","expire"])
def test_revocation_and_expiry_race(pg,action):
    settings=get_settings()
    with pg() as db: issued=grant(db)
    def operation(db,index):
        if index==0: return service.consume(db,issued["ticket"],"http://localhost:5600","owner",settings)
        if action=="revoke": service.revoke(db,1)
        else:
            service.lock_actor(db,1)
            row=db.get(Live,issued["session_id"])
            row.expires_at=service.now()-timedelta(seconds=1)
        db.commit();return action
    race(pg,operation)
    with pg() as db:
        service.sweep(db,settings)
        row=db.get(Live,issued["session_id"],populate_existing=True)
        assert row.state in {"revoked","ended"} and row.lease_owner is None
