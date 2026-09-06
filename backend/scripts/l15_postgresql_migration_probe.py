"""Destructive only to the empty L15 table in an explicitly named temporary DB."""
import json
import os
from pathlib import Path
import subprocess
import sys

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url


def main():
    url=os.environ["L15_TEST_POSTGRES_URL"]
    parsed=make_url(url)
    if parsed.host not in {"127.0.0.1","localhost"} or not parsed.database.startswith("l15_test"):
        raise SystemExit("Only a localhost disposable l15_test database is allowed")
    engine=create_engine(url)
    with engine.connect() as c:
        assert c.execute(text("SELECT current_database()")).scalar_one()==parsed.database
        assert c.execute(text("SELECT count(*) FROM realtime_sessions")).scalar_one()==0
    def migration(*args):
        env=dict(os.environ,DATABASE_URL=url,PYTHONDONTWRITEBYTECODE="1")
        subprocess.run([sys.executable,"-B","-m","alembic",*args],env=env,check=True)
    def snapshot():
        with engine.connect() as c:
            return {name:c.exec_driver_sql('SELECT * FROM "'+name+'"').fetchall()
                    for name in inspect(c).get_table_names() if name not in {"alembic_version","realtime_sessions"}}
    migration("downgrade","0015_conversation_turns")
    with engine.begin() as c:
        c.execute(text("INSERT INTO users(id,full_name,email,password_hash) VALUES(700,'Migration sentinel','l15-migration@example.com','not-a-password')"))
        c.execute(text("INSERT INTO legacies(id,owner_user_id,subject_name,setup_status) VALUES(700,700,'Disposable','active')"))
        c.execute(text("INSERT INTO conversations(id,user_id,legacy_id,mode,title) VALUES(700,700,700,'rya','Migration sentinel')"))
        c.execute(text("INSERT INTO messages(conversation_id,role,content) VALUES(700,'user','Disposable historical transcript')"))
    before=snapshot()
    migration("upgrade","head")
    assert snapshot()==before
    migration("downgrade","0015_conversation_turns")
    assert snapshot()==before
    migration("upgrade","head")
    assert snapshot()==before
    with engine.connect() as c:
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar_one()=="0016_realtime_sessions"
        assert "active_actor_id IS NOT NULL" in next(x["sqltext"] for x in inspect(c).get_check_constraints("realtime_sessions") if x["name"]=="ck_realtime_terminal")
    result={"database":parsed.database,"historical_tables_preserved":len(before),"upgrade":True,"downgrade":True,"reupgrade":True}
    Path("docs/L15_POSTGRESQL_MIGRATION.json").write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps(result))
    engine.dispose()


if __name__=="__main__": main()
