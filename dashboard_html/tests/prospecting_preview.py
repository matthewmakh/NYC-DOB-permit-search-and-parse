"""Local browser QA with REAL prospecting APIs and a disposable schema.

PROSPECTING_TEST_DATABASE_URL=postgresql://127.0.0.1:55443/postgres \
  uv run --with flask --with psycopg2-binary --with requests python dashboard_html/tests/prospecting_preview.py
No production app import, credentials, data, or .env. Binds only to localhost.
"""
import atexit
import os
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse

import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, session, abort

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import auth_service
import crm_service as crm
from crm_routes import crm_bp
import prospecting_routes as routes

dsn=os.environ['PROSPECTING_TEST_DATABASE_URL']
if urlparse(dsn).hostname not in ('127.0.0.1','localhost'):
    raise RuntimeError('Preview requires a disposable localhost database.')
schema='prospect_preview_'+uuid.uuid4().hex
admin=psycopg2.connect(dsn);admin.autocommit=True
with admin.cursor() as cur:
    cur.execute(f'CREATE SCHEMA {schema}')
    cur.execute(f'CREATE TABLE {schema}.users(id INTEGER PRIMARY KEY,email TEXT,is_admin BOOLEAN,last_login TIMESTAMP)')
    cur.execute(f"INSERT INTO {schema}.users VALUES(1,'preview@example.test',true,NULL),(2,'alex@example.test',false,NULL),(3,'sam@example.test',false,NULL)")
    cur.execute(f'CREATE TABLE {schema}.account_sponsorships(sponsor_user_id INTEGER,member_user_id INTEGER,status TEXT,display_name TEXT,accepted_at TIMESTAMP)')
    cur.execute(f"INSERT INTO {schema}.account_sponsorships VALUES(1,2,'active','Alex',NOW()),(1,3,'active','Sam',NOW())")

def cleanup():
    with admin.cursor() as cur:cur.execute(f'DROP SCHEMA {schema} CASCADE')
    admin.close()
atexit.register(cleanup)
crm.get_db_connection=lambda:psycopg2.connect(dsn,options=f'-c search_path={schema}',cursor_factory=RealDictCursor)
crm.init_crm_tables()
def fixture_user(token):
    user_id=session.get('preview_user',1)
    return dict(id=user_id,is_admin=user_id==1,is_sponsored=user_id!=1,sponsor_user_id=1,email=f'preview{user_id}@example.test')
auth_service.validate_session=fixture_user
routes._base_template_args=lambda ctx,tab:dict(active_page='crm',crm_tab=tab,crm_ctx=ctx,crm_user_name='Preview user',
    due_count=0,notification_count=0,push_public_key='',default_reminder_minutes=15,
    stages=crm.STAGES,stage_labels=crm.STAGE_LABELS,method_labels=crm.METHOD_LABELS,
    outcome_labels=crm.OUTCOME_LABELS,building_roles=crm.BUILDING_CONTACT_ROLES,today_ny=crm.ny_today())
app=Flask(__name__,template_folder=str(ROOT/'templates'),static_folder=str(ROOT/'static'))
app.secret_key='local-prospecting-preview-only'
app.config['TEMPLATES_AUTO_RELOAD']=True
app.register_blueprint(crm_bp)
app.register_blueprint(routes.prospecting_bp)
app.add_url_rule('/auth/login',endpoint='auth.login',view_func=lambda:'Local preview')
# Avoid unrelated notification requests during local browser QA.
app.add_url_rule('/crm/api/push/config',view_func=lambda:dict(success=True,enabled=False))

@app.post('/__test/user/<int:user_id>')
def switch_test_user(user_id):
    if user_id not in (1,2,3):abort(404)
    session['preview_user']=user_id
    return dict(success=True)

if __name__=='__main__':app.run(host='127.0.0.1',port=5101)
