from datetime import date, datetime
from unittest.mock import Mock
from zipfile import ZipFile
import json

import pytest

from config_store import ConfigStore
from energy_usage import KOREA
from home_service import HomeService, HomeError
from usage_archive import UsageArchive
from webapp import create_app


@pytest.fixture
def archive(tmp_path):
    home = HomeService()
    home.api = Mock(complex_id='complex', username='user', access_token='ACCESS', refresh_token='REFRESH')
    return UsageArchive(tmp_path/'usage.sqlite3',home)


def raw(value='100', day='2026-11-20', unit='kWh'):
    return {'Resources':[{'Start_Time':day,'Amount':value,'Unit':unit,'Access_Token':'SECRET'}]}


def add(archive, value='100', day='2026-11-20', unit='kWh'):
    return archive.ingest('Electricity',date.fromisoformat(day),date.fromisoformat(day),raw(value,day,unit),'test')


def current(archive):
    return archive.history('Electricity','2026-11-20')['current']


def test_threshold_zero_unit_and_raw_immutable(archive):
    add(archive)
    add(archive,'119.9')
    assert current(archive)['amount']=='119.9'
    add(archive,'143.88')  # exactly +20%
    assert current(archive)['amount']=='119.9'
    review = archive.reviews()[0]
    assert review['candidate']=='143.88' and review['old_amount']=='119.9'
    history = archive.history('Electricity','2026-11-20')
    assert len(history['observations'])==3
    assert 'SECRET' not in str(history)
    archive.resolve({'id':review['id'],'action':'accept','reason':'앱과 비교 확인'})
    assert current(archive)['amount']=='143.88'
    add(archive,'143.88',unit='Wh')
    assert archive.reviews()[0]['reason']=='단위 변경'
    add(archive,'0',day='2026-11-19')
    add(archive,'1',day='2026-11-19')
    assert archive.reviews()[0]['reason']=='기존 값 대비 20% 이상 변화'


@pytest.mark.parametrize('value,pending',[('80',True),('79.99',True),('80.001',False),('120',True),('119.999',False)])
def test_exact_threshold_both_directions(archive,value,pending):
    add(archive)
    add(archive,value)
    assert bool(archive.reviews()) is pending


def test_today_percentage_exempt_but_past_day_checked(archive,monkeypatch):
    monkeypatch.setattr('usage_archive.today',lambda:date(2026,11,23))
    add(archive,'0',day='2026-11-23')
    add(archive,'100',day='2026-11-23')
    add(archive,'10',day='2026-11-23')
    assert not archive.reviews()
    assert archive.history('Electricity','2026-11-23')['current']['amount']=='10'
    monkeypatch.setattr('usage_archive.today',lambda:date(2026,11,24))
    add(archive,'20',day='2026-11-23')
    assert not archive.reviews()
    assert archive.history('Electricity','2026-11-23')['current']['amount']=='20'
    monkeypatch.setattr('usage_archive.today',lambda:date(2026,11,25))
    add(archive,'40',day='2026-11-23')
    assert archive.reviews()[0]['reason']=='기존 값 대비 20% 이상 변화'


def test_today_manual_values_unit_and_invalid_values_still_protected(archive,monkeypatch):
    monkeypatch.setattr('usage_archive.today',lambda:date(2026,11,20))
    add(archive)
    archive.edit({'category':'Electricity','day':'2026-11-20','amount':'95','unit':'kWh','version':1,'reason':'검침'})
    add(archive,'200')
    assert current(archive)['amount']=='95'
    assert archive.reviews()[0]['reason']=='직접 수정한 값과 다른 재조회 값'
    add(archive,'-1')
    assert current(archive)['amount']=='95'
    add(archive,'50',day='2026-11-19')
    # Move the household date to this record's day to exercise a unit-only change.
    monkeypatch.setattr('usage_archive.today',lambda:date(2026,11,19))
    add(archive,'50',day='2026-11-19',unit='Wh')
    assert archive.reviews()[0]['reason']=='단위 변경'


def test_todays_legacy_percentage_holds_retired_without_deleting_history(archive,monkeypatch):
    monkeypatch.setattr('usage_archive.today',lambda:date(2026,11,25))
    add(archive,'100',day='2026-11-23')
    add(archive,'150',day='2026-11-23')
    add(archive,'110',day='2026-11-23')
    assert len(archive.reviews())==2
    monkeypatch.setattr('usage_archive.today',lambda:date(2026,11,24))
    add(archive,'170',day='2026-11-23')
    assert not archive.reviews()
    assert archive.history('Electricity','2026-11-23')['current']['amount']=='170'
    with archive.db() as db:
        assert db.execute("SELECT COUNT(*) FROM reviews WHERE status='exempt'").fetchone()[0]==2


@pytest.mark.parametrize('reference,previous',[(date(2026,1,1),'2025-12-31'),(date(2024,3,1),'2024-02-29')])
def test_yesterday_exemption_across_year_and_leap_month(archive,monkeypatch,reference,previous):
    monkeypatch.setattr('usage_archive.today',lambda:reference)
    add(archive,'0',day=previous)
    add(archive,'100',day=previous)
    assert not archive.reviews()
    assert archive.history('Electricity',previous)['current']['amount']=='100'


def test_monthly_totals_use_edited_daily_values_and_mark_missing_leap_day(archive):
    rows=[{'Start_Time':f'2024-02-{day:02d}','Amount':'1.1','Unit':'kWh'} for day in range(1,29)]
    archive.ingest('Electricity',date(2024,2,1),date(2024,2,29),{'Resources':rows},'initial')
    data=archive.query('Electricity','Month','2024','0')['current']['points'][0]
    assert data['amount']==30.8 and data['days']==28 and data['expected_days']==29 and data['partial']
    archive.edit({'category':'Electricity','day':'2024-02-29','amount':'2.2','unit':'kWh','version':0,'reason':'검침 확인'})
    data=archive.query('Electricity','Month','2024','0')['current']['points'][0]
    assert data['amount']==33 and data['days']==29 and not data['partial']


def test_bad_and_missing_records_do_not_erase_data(archive):
    add(archive)
    add(archive,'-100')
    assert current(archive)['amount']=='100'
    archive.ingest('Electricity',date(2026,11,20),date(2026,11,20),{'Resources':[]},'daily')
    assert current(archive)['amount']=='100'
    assert any('누락' in r['reason'] for r in archive.reviews())
    invalid = next(r for r in archive.reviews() if not r['valid'])
    with pytest.raises(HomeError):
        archive.resolve({'id':invalid['id'],'action':'accept','reason':'invalid'})
    archive.resolve({'id':invalid['id'],'action':'keep','reason':'기존 값 확인'})


def test_manual_edit_lock_conflict_and_aggregation(archive):
    add(archive)
    version=current(archive)['version']
    archive.edit({'category':'Electricity','day':'2026-11-20','amount':'90','unit':'kWh','version':version,'reason':'검침기 확인'})
    add(archive,'91') # under threshold but manually corrected reading stays locked
    assert current(archive)['amount']=='90'
    assert current(archive)['locked']==1
    assert archive.reviews()[0]['reason']=='직접 수정한 값과 다른 재조회 값'
    with pytest.raises(HomeError) as error:
        archive.edit({'category':'Electricity','day':'2026-11-20','amount':'80','unit':'kWh','version':version,'reason':'stale'})
    assert error.value.status==409
    result=archive.query('Electricity','Month','2026','0')
    assert result['current']['points'][0]['amount']==90
    assert result['current']['points'][0]['partial'] is True
    assert result['current']['points'][0]['pending'] is True
    archive.home.api._send_request.assert_not_called() # reads use only storage
    other=UsageArchive(archive.path,archive.home)
    assert current(other)['amount']=='90'


def test_initial_plan_daily_schedule_and_resume(archive):
    archive.plan(datetime(2026,11,23,2,59,tzinfo=KOREA))
    status=archive.status()
    assert status['tasks']['pending']==20 # 4 years x 5 categories
    archive.plan(datetime(2026,11,23,3,0,tzinfo=KOREA))
    assert archive.status()['tasks']['pending']==25
    archive.plan(datetime(2026,11,23,3,1,tzinfo=KOREA))
    assert archive.status()['tasks']['pending']==25
    archive.home.api._send_request.return_value={'Resources':[]}
    assert archive.process_one()
    args=archive.home.api._send_request.call_args.args
    assert args[0]=='/used/list'
    assert args[1]['Start_Time']=='2026-11-20T00:00:00'
    assert args[1]['End_Time']=='2026-11-22T23:59:59'
    assert args[1]['Query_Type']=='Day'
    assert archive.status()['tasks']['empty']==1
    with archive.db() as db:
        db.execute("UPDATE tasks SET state='running' WHERE id=(SELECT MIN(id) FROM tasks WHERE state='pending')")
    resumed=UsageArchive(archive.path,archive.home)
    assert resumed.status()['tasks'].get('running',0)==0


def test_failure_retry_and_web_range(archive,monkeypatch):
    monkeypatch.setattr('usage_archive.today',lambda:date(2026,11,23))
    archive.home.api._send_request.return_value={'Resources':[]}
    archive.refresh('Water')
    fields=archive.home.api._send_request.call_args.args[1]
    assert fields['Start_Time']=='2026-11-21T00:00:00'
    assert fields['End_Time']=='2026-11-23T23:59:59'
    archive.enqueue('daily',date(2026,11,20),date(2026,11,22))
    archive.home.api._send_request.side_effect=HomeError('remote failed',502)
    archive.process_one()
    assert archive.status()['tasks']['failed']==1
    archive.retry()
    assert not archive.status()['tasks'].get('failed')
    archive.home.api.login.assert_not_called()
    archive.home.api.get_token.assert_not_called()


def test_stale_review_does_not_overwrite_later_edit(archive):
    add(archive)
    add(archive,'150')
    review=archive.reviews()[0]
    archive.edit({'category':'Electricity','day':'2026-11-20','amount':'95','unit':'kWh','version':1,'reason':'정정'})
    with pytest.raises(HomeError) as error:
        archive.resolve({'id':review['id'],'action':'accept','reason':'old'})
    assert error.value.status==409
    assert current(archive)['amount']=='95'


def test_pending_review_freezes_baseline_until_decision(archive):
    add(archive)
    add(archive,'130')
    add(archive,'110')
    assert current(archive)['amount']=='100'
    assert len(archive.reviews())==2


def test_export_snapshot_audit_and_no_credentials(archive):
    add(archive)
    archive.edit({'category':'Electricity','day':'2026-11-20','amount':'95','unit':'kWh','version':1,'reason':'=formula'})
    with ZipFile(archive.export()) as export:
        assert set(export.namelist())=={'readings.csv','observations.csv','edits.csv','reviews.csv','batches.csv','tasks.csv'}
        text=''.join(export.read(name).decode('utf-8-sig') for name in export.namelist())
        assert 'SECRET' not in text and 'ACCESS' not in text and 'REFRESH' not in text
        assert "'=formula" in text
        assert '95' in text


def test_daily_downtime_catches_up_without_skipping_days(archive):
    with archive.db() as db:
        archive.check_owner(db,bind=True)
        db.execute("INSERT INTO meta VALUES ('initial_planned','2023')")
        db.execute("INSERT INTO tasks(category,start,end,kind,state) VALUES ('Electricity','2026-11-01','2026-11-03','daily','done')")
    archive.plan(datetime(2026,11,23,3,0,tzinfo=KOREA))
    with archive.db() as db:
        row=db.execute("SELECT start,end FROM tasks WHERE state='pending' LIMIT 1").fetchone()
    assert tuple(row)==('2026-11-04','2026-11-22')


def test_other_house_cannot_read_archive(archive):
    add(archive)
    archive.home.api.username='another-user'
    with pytest.raises(HomeError):
        archive.query('Electricity','Day','2026-11','0')


def test_duplicate_response_not_arbitrarily_accepted(archive):
    payload=raw();payload['Resources'].append(dict(payload['Resources'][0]))
    archive.ingest('Electricity',date(2026,11,20),date(2026,11,20),payload,'test')
    assert current(archive) is None
    assert len(archive.reviews())==1
    assert len(archive.history('Electricity','2026-11-20')['observations'])==2


@pytest.mark.parametrize('action,expected',[('keep','0.1'),('accept','0.2')])
def test_legacy_three_identical_hotwater_reviews_resolve_once(archive,monkeypatch,action,expected):
    monkeypatch.setattr('usage_archive.today',lambda:date(2026,11,25))
    start=date(2026,11,23)
    def ingest(value):
        archive.ingest('Hotwater',start,start,raw(value,'2026-11-23','㎥'),'test')
    ingest('0.1');ingest('0.2')
    original=archive.reviews()[0]
    with archive.db() as db:
        for _ in range(2):
            observation=db.execute('INSERT INTO observations(batch,category,day,amount,unit,valid,reason,raw,observed) SELECT batch,category,day,amount,unit,valid,reason,raw,observed FROM observations WHERE id=?',(original['observation'],)).lastrowid
            db.execute('INSERT INTO reviews(observation,category,day,old_amount,old_unit,base_version,reason) SELECT ?,category,day,old_amount,old_unit,base_version,? FROM reviews WHERE id=?',(observation,'이전 변동 검토 대기 중 추가 변경',original['id']))
    assert len(archive.reviews())==3
    result=archive.resolve({'id':original['id'],'action':action,'reason':'사용자 확인'})
    assert len(result['resolved_ids'])==3
    assert archive.reviews()==[]
    assert archive.history('Hotwater','2026-11-23')['current']['amount']==expected
    ingest('0.2');ingest('0.2')
    assert archive.reviews()==[]
    assert len(archive.history('Hotwater','2026-11-23')['observations'])==6


def test_distinct_candidates_not_resolved_together(archive):
    add(archive);add(archive,'150');add(archive,'160')
    item=next(r for r in archive.reviews() if r['candidate']=='150')
    archive.resolve({'id':item['id'],'action':'keep','reason':'확인'})
    assert [r['candidate'] for r in archive.reviews()]==['160']
    add(archive,'150')
    assert [r['candidate'] for r in archive.reviews()]==['160']


def test_already_applied_candidate_clears_stale_duplicate_without_new_edit(archive):
    add(archive);add(archive,'150')
    review=archive.reviews()[0]
    archive.edit({'category':'Electricity','day':'2026-11-20','amount':'150','unit':'kWh','version':1,'reason':'기존에 채택한 값'})
    version=current(archive)['version']
    archive.resolve({'id':review['id'],'action':'accept','reason':'중복 정리'})
    assert archive.reviews()==[] and current(archive)['version']==version


def test_api_auth_csrf_and_no_network_get(tmp_path):
    home=HomeService()
    home.api=Mock(complex_id='c',username='u',access_token='A',refresh_token='R')
    app=create_app({'TESTING':True,'DEMO':False,'ADMIN_PIN':'012345','SECRET_KEY':'test',
                    'CONFIG_DIR':str(tmp_path),'DATA_DIR':str(tmp_path),'START_SCHEDULER':False,
                    'SESSION_COOKIE_SECURE':False},service=home)
    try:
        c=app.test_client()
        assert c.get('/api/usage/status').status_code==401
        csrf=c.get('/api/session').json['csrf']
        csrf=c.post('/api/login',json={'pin':'012345'},headers={'X-CSRF-Token':csrf}).json['csrf']
        assert c.get('/api/usage?category=Electricity&period=Day&selected=2026-11').status_code==200
        home.api._send_request.assert_not_called()
        assert c.post('/api/usage/refresh',json={'category':'Electricity'}).status_code==403
        response=c.post('/api/usage/edit',json={'category':'Electricity','day':'2026-11-20','amount':'1','unit':'kWh','version':0,'reason':'확인'},headers={'X-CSRF-Token':csrf})
        assert response.status_code==200
        assert c.get('/api/usage/history?category=Electricity&day=2026-11-20').json['current']['locked']==1
        assert c.post('/api/usage/refresh',json={'category':[]},headers={'X-CSRF-Token':csrf}).status_code==400
    finally:
        app.extensions['usage_archive'].close();app.extensions['jobs'].close()
