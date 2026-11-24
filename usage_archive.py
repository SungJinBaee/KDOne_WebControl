"""Persistent daily observations, reviewed readings and resumable collection.

Only accepted daily readings contribute to monthly totals. Raw observations,
including bad/missing values, are immutable. One process owns the collector.
"""
import calendar
import hashlib
import io
import csv
import json
import sqlite3
import threading
import time
from zipfile import ZipFile, ZIP_DEFLATED
from collections import defaultdict
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from energy_usage import CATEGORIES, KOREA, reading_date, query_ranges, demo_payload
from home_service import HomeError


def today():
    return datetime.now(KOREA).date()


def amount(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError('사용량은 0 이상의 숫자여야 합니다.')
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0 or result > Decimal('1e15'):
            raise ValueError
        return format(result.normalize(),'f') if result else '0'
    except (InvalidOperation, ValueError):
        raise ValueError('사용량은 0 이상 10^15 이하의 유한한 숫자여야 합니다.') from None


class UsageArchive:
    def __init__(self, path, home):
        self.path, self.home = Path(path), home
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock, self.collection_lock = threading.RLock(), threading.Lock()
        self.stop = threading.Event()
        self.worker_error = ''
        with self.db() as db:
            db.executescript('''
              PRAGMA journal_mode=WAL;
              CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS batches (id INTEGER PRIMARY KEY, category TEXT, start TEXT, end TEXT,
                kind TEXT, fetched REAL, raw TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS readings (category TEXT, day TEXT, amount TEXT, unit TEXT,
                version INTEGER NOT NULL, locked INTEGER NOT NULL DEFAULT 0, origin TEXT, updated REAL,
                PRIMARY KEY(category,day));
              CREATE TABLE IF NOT EXISTS observations (id INTEGER PRIMARY KEY, batch INTEGER, category TEXT,
                day TEXT, amount TEXT, unit TEXT, valid INTEGER, reason TEXT, raw TEXT, observed REAL);
              CREATE INDEX IF NOT EXISTS obs_day ON observations(category,day,id);
              CREATE TABLE IF NOT EXISTS reviews (id INTEGER PRIMARY KEY, observation INTEGER UNIQUE,
                category TEXT, day TEXT, old_amount TEXT, old_unit TEXT, base_version INTEGER,
                reason TEXT, status TEXT NOT NULL DEFAULT 'pending', note TEXT, resolved REAL);
              CREATE TABLE IF NOT EXISTS edits (id INTEGER PRIMARY KEY, category TEXT, day TEXT,
                old_amount TEXT, old_unit TEXT, amount TEXT, unit TEXT, reason TEXT, at REAL, origin TEXT);
              CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY, category TEXT, start TEXT, end TEXT,
                kind TEXT, state TEXT NOT NULL DEFAULT 'pending', error TEXT, updated REAL,
                UNIQUE(category,start,end,kind));
              CREATE INDEX IF NOT EXISTS reviews_day ON reviews(category,day,status);
            ''')
            db.execute("UPDATE tasks SET state='pending' WHERE state='running'")

    @contextmanager
    def db(self):
        with self.lock:
            conn = sqlite3.connect(self.path, timeout=10)
            conn.row_factory = sqlite3.Row
            try:
                with conn:
                    yield conn
            finally:
                conn.close()

    def owner(self):
        if self.home.demo:
            return 'demo'
        store = self.home.config_store
        account = store.secrets().get('account', {}) if store else {}
        complex_id = account.get('complex_id') or getattr(self.home.api, 'complex_id', '')
        user = account.get('username') or getattr(self.home.api, 'username', '')
        if not isinstance(complex_id, str) or not isinstance(user, str) or not complex_id or not user:
            return None
        return hashlib.sha256((complex_id+'\0'+user).encode()).hexdigest()

    def check_owner(self, db, bind=False):
        owner = self.owner()
        row = db.execute("SELECT value FROM meta WHERE key='owner'").fetchone()
        if row and owner != row[0]:
            raise HomeError('저장된 사용량의 세대 계정과 현재 계정이 다릅니다.', 409)
        if bind and not row:
            if not owner:
                raise HomeError('세대 계정 연결 후 수집하세요.', 409)
            db.execute("INSERT INTO meta VALUES ('owner',?)", (owner,))

    @staticmethod
    def same_candidate(left, right):
        if bool(left['valid']) != bool(right['valid']):
            return False
        if left['valid']:
            return left['candidate_unit'] == right['candidate_unit'] and Decimal(left['candidate']) == Decimal(right['candidate'])
        return left['raw'] == right['raw'] and left['reason'] == right['reason']

    @staticmethod
    def related_reviews(db, category, day, status):
        return db.execute('SELECT r.*,o.amount candidate,o.unit candidate_unit,o.valid,o.raw FROM reviews r JOIN observations o ON o.id=r.observation WHERE r.category=? AND r.day IS ? AND r.status=?',(category,day,status)).fetchall()

    @staticmethod
    def valid_key(category, day):
        try:
            parsed = date.fromisoformat(day)
        except (TypeError, ValueError):
            raise HomeError('날짜는 YYYY-MM-DD 형식이어야 합니다.') from None
        if not isinstance(category,str) or category not in CATEGORIES or not date(2023, 1, 1) <= parsed <= today():
            raise HomeError('종류와 날짜를 확인하세요. 2023년 이후 기록만 수정할 수 있습니다.')
        return parsed

    def ingest(self, category, start, end, payload, kind):
        rows = payload.get('Resources') if isinstance(payload, dict) else None
        if not isinstance(rows, list) or len(rows) > 800:
            raise HomeError('사용량 원본 목록 형식이 다릅니다.', 502)
        # Persist only measurement fields, never tokens or account response envelopes.
        safe = [{k: row.get(k) if type(row.get(k)) in (str,int,float,bool,type(None)) else '[invalid type]'
                 for k in ('Start_Time','Amount','Unit')} for row in rows if isinstance(row, dict)]
        grouped, parsed = defaultdict(list), []
        for row in rows:
            record = {'raw': {k: row.get(k) if type(row.get(k)) in (str,int,float,bool,type(None)) else '[invalid type]' for k in ('Start_Time','Amount','Unit')} if isinstance(row, dict) else {'invalid_row': True},
                      'day': None, 'amount': None, 'unit': None, 'valid': False, 'reason': ''}
            try:
                if not isinstance(row, dict):
                    raise ValueError('잘못된 원본 항목')
                day = reading_date(row.get('Start_Time'))
                record['day'] = day.isoformat()
                if not start <= day <= end:
                    raise ValueError('요청 기간 밖 날짜')
                record['amount'] = amount(row.get('Amount'))
                unit = row.get('Unit')
                if not isinstance(unit, str) or not unit.strip() or len(unit) > 24:
                    raise ValueError('단위 없음 또는 잘못된 단위')
                record['unit'], record['valid'] = unit, True
            except ValueError as error:
                record['reason'] = str(error)
            parsed.append(record)
            if record['day']:
                grouped[record['day']].append(record)
        for group in grouped.values():
            if len(group) > 1:
                for record in group:
                    record.update(valid=False, reason='같은 날짜의 중복 응답')
        now = time.time()
        reference_day = today()
        percentage_exempt_days = {reference_day.isoformat(), (reference_day-timedelta(days=1)).isoformat()}
        with self.db() as db:
            self.check_owner(db, bind=True)
            batch = db.execute('INSERT INTO batches(category,start,end,kind,fetched,raw) VALUES (?,?,?,?,?,?)',
                               (category,start.isoformat(),end.isoformat(),kind,now,json.dumps(safe,ensure_ascii=False))).lastrowid
            existing = {r['day']: r for r in db.execute('SELECT * FROM readings WHERE category=? AND day BETWEEN ? AND ?',
                        (category,start.isoformat(),end.isoformat()))}
            for day in existing.keys() - grouped.keys():
                parsed.append({'day':day,'amount':None,'unit':None,'valid':False,'reason':'재조회 응답에서 기록 누락','raw':{}})
            for record in parsed:
                day, value, unit = record['day'], record['amount'], record['unit']
                old = existing.get(day)
                reason = record['reason']
                percentage_exempt = day in percentage_exempt_days
                # Retain the audit but retire recent percentage-only holds
                # when a fresh, valid observation is available under the new rule.
                if percentage_exempt and record['valid']:
                    pending = db.execute("SELECT id,reason FROM reviews WHERE category=? AND day=? AND status='pending'",(category,day)).fetchall()
                    percentage_reasons = ('기존 값 대비 20% 이상 변화','이전 변동 검토 대기 중 추가 변경')
                    if pending and any(r['reason']==percentage_reasons[0] for r in pending) and all(r['reason'] in percentage_reasons for r in pending):
                        db.executemany("UPDATE reviews SET status='exempt',note='조회·집계 시점의 오늘·어제 20% 변동 검토 제외; 새 수집값으로 재평가',resolved=? WHERE id=?",[(now,r['id']) for r in pending])
                if record['valid'] and old:
                    if value == old['amount'] and unit == old['unit']:
                        pass
                    elif old['locked']:
                        reason = '직접 수정한 값과 다른 재조회 값'
                    elif db.execute("SELECT 1 FROM reviews WHERE category=? AND day=? AND status='pending' LIMIT 1",(category,day)).fetchone():
                        reason = '이전 변동 검토 대기 중 추가 변경'
                    elif unit != old['unit']:
                        reason = '단위 변경'
                    elif not percentage_exempt and ((Decimal(old['amount']) == 0 and Decimal(value) != 0) or abs(Decimal(value)-Decimal(old['amount'])) >= abs(Decimal(old['amount'])) * Decimal('.20')):
                        reason = '기존 값 대비 20% 이상 변화'
                observation = db.execute('INSERT INTO observations(batch,category,day,amount,unit,valid,reason,raw,observed) VALUES (?,?,?,?,?,?,?,?,?)',
                    (batch,category,day,value,unit,int(record['valid']),reason,json.dumps(record['raw'],ensure_ascii=False),now)).lastrowid
                candidate = {'candidate':value,'candidate_unit':unit,'valid':record['valid'],
                             'raw':json.dumps(record['raw'],ensure_ascii=False),'reason':reason}
                kept = old and any(r['base_version']==old['version'] and self.same_candidate(r,candidate)
                                   for r in self.related_reviews(db,category,day,'keep'))
                duplicate = reason and any(r['base_version']==(old['version'] if old else 0) and self.same_candidate(r,candidate)
                                            for r in self.related_reviews(db,category,day,'pending'))
                if reason and not kept and not duplicate:
                    db.execute('INSERT INTO reviews(observation,category,day,old_amount,old_unit,base_version,reason) VALUES (?,?,?,?,?,?,?)',
                        (observation,category,day,old['amount'] if old else None,old['unit'] if old else None,old['version'] if old else 0,reason))
                elif not reason and not kept and record['valid'] and (not old or value != old['amount'] or unit != old['unit']):
                    self.write(db, category, day, value, unit, 'server', '정상 수집', old, locked=False)
        return len(rows)

    @staticmethod
    def write(db, category, day, value, unit, origin, reason, old, locked):
        version = old['version']+1 if old else 1
        now = time.time()
        db.execute('INSERT INTO edits(category,day,old_amount,old_unit,amount,unit,reason,at,origin) VALUES (?,?,?,?,?,?,?,?,?)',
                   (category,day,old['amount'] if old else None,old['unit'] if old else None,value,unit,reason,now,origin))
        db.execute('INSERT INTO readings VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(category,day) DO UPDATE SET amount=excluded.amount,unit=excluded.unit,version=excluded.version,locked=excluded.locked,origin=excluded.origin,updated=excluded.updated',
                   (category,day,value,unit,version,int(locked),origin,now))

    def fetch(self, category, start, end, kind):
        with self.home.lock:
            if not self.home.connected:
                raise HomeError('연결되지 않아 수집을 보류했습니다.', 409)
            owner = self.owner()
            if self.home.demo:
                payload = demo_payload(category,start,end,'Day')
            else:
                api = self.home.api
                payload = self.home._network(lambda: api._send_request('/used/list', {
                    'Complex':api.complex_id,'Access_Token':api.access_token,'Refresh_Token':api.refresh_token,
                    'Category_Type':category,'Query_Type':'Day','Start_Time':start.isoformat()+'T00:00:00',
                    'End_Time':end.isoformat()+'T23:59:59'}))
        if self.owner() != owner:
            raise HomeError('수집 도중 세대 계정이 변경되어 저장하지 않았습니다.',409)
        return self.ingest(category,start,end,payload,kind)

    def refresh(self, category):
        if not isinstance(category,str) or category not in CATEGORIES:
            raise HomeError('사용량 종류를 확인하세요.')
        if not self.collection_lock.acquire(timeout=45):
            return {'message':'다른 수집이 진행 중입니다. 저장된 집계로 표시합니다.', 'refreshed':False}
        try:
            end = today()
            count = self.fetch(category,end-timedelta(days=2),end,'web')
            return {'message':f'당일과 최근 2일 재조회 완료: {count}건. 검토 대기 값은 기존 확정값을 유지합니다.', 'refreshed':True}
        finally:
            self.collection_lock.release()

    def enqueue(self, kind, start, end):
        with self.db() as db:
            self.check_owner(db, bind=True)
            for year in range(start.year,end.year+1):
                left, right = max(start,date(year,1,1)), min(end,date(year,12,31))
                for category in CATEGORIES:
                    db.execute('INSERT OR IGNORE INTO tasks(category,start,end,kind,updated) VALUES (?,?,?,?,?)',
                               (category,left.isoformat(),right.isoformat(),kind,time.time()))

    def plan(self, now=None):
        now = now or datetime.now(KOREA)
        if not self.home.connected or self.home.demo:
            return
        with self.db() as db:
            self.check_owner(db, bind=True)
            planned = db.execute("SELECT 1 FROM meta WHERE key='initial_planned'").fetchone()
        if not planned:
            self.enqueue('initial',date(2023,1,1),now.date())
            with self.db() as db:
                db.execute("INSERT INTO meta VALUES ('initial_planned','2023')")
        if now.hour >= 3:
            end = now.date()-timedelta(days=1)
            with self.db() as db:
                last = db.execute("SELECT MAX(end) FROM tasks WHERE kind='daily' AND state IN ('done','empty')").fetchone()[0]
            start = min(end-timedelta(days=2),date.fromisoformat(last)+timedelta(days=1)) if last else end-timedelta(days=2)
            self.enqueue('daily',max(start,date(2023,1,1)),end)

    def process_one(self):
        if not self.home.connected or not self.collection_lock.acquire(blocking=False):
            return False
        try:
            with self.db() as db:
                self.check_owner(db)
                task = db.execute("SELECT * FROM tasks WHERE state='pending' ORDER BY CASE kind WHEN 'daily' THEN 0 ELSE 1 END,id LIMIT 1").fetchone()
                if not task:
                    return False
                db.execute("UPDATE tasks SET state='running',updated=? WHERE id=?", (time.time(),task['id']))
            try:
                count = self.fetch(task['category'],date.fromisoformat(task['start']),date.fromisoformat(task['end']),task['kind'])
                state, error = ('done' if count else 'empty'), None
            except HomeError as exc:
                state, error = 'failed', str(exc)
            except Exception as exc:
                state, error = 'failed', '수집 처리 오류: '+type(exc).__name__
            with self.db() as db:
                db.execute('UPDATE tasks SET state=?,error=?,updated=? WHERE id=?',(state,error,time.time(),task['id']))
            return True
        finally:
            self.collection_lock.release()

    def start(self):
        def run():
            while not self.stop.is_set():
                try:
                    self.plan()
                    busy = self.process_one()
                    self.worker_error = ''
                except Exception as exc:
                    self.worker_error = '수집 작업 오류: '+type(exc).__name__
                    busy = False
                self.stop.wait(2 if busy else 15)
        self.thread = threading.Thread(target=run,name='usage-collector',daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if hasattr(self,'thread'):
            self.thread.join(timeout=45)

    def status(self):
        with self.db() as db:
            self.check_owner(db)
            counts = {r[0]:r[1] for r in db.execute('SELECT state,COUNT(*) FROM tasks GROUP BY state')}
            return {'start_year':2023,'daily_at':'03:00','lookback_days':3,'threshold_percent':20,'exclude_today_from_threshold':True,'percentage_exempt_days':2,
                'tasks':counts,'readings':db.execute('SELECT COUNT(*) FROM readings').fetchone()[0],
                'pending_reviews':db.execute("SELECT COUNT(*) FROM reviews WHERE status='pending'").fetchone()[0],
                'last_fetch':db.execute('SELECT MAX(fetched) FROM batches').fetchone()[0],
                'error':self.worker_error,
                'failed_tasks':[dict(r) for r in db.execute("SELECT category,start,end,error FROM tasks WHERE state='failed' ORDER BY id DESC LIMIT 20")]}

    def retry(self):
        with self.db() as db:
            self.check_owner(db)
            db.execute("UPDATE tasks SET state='pending',error=NULL WHERE state IN ('failed','empty')")
        return {'message':'실패·빈 구간을 재수집 대기열에 넣었습니다.'}

    def export(self):
        """Consistent CSV export, including immutable observations and edit audit."""
        output = io.BytesIO()
        with self.db() as db:
            self.check_owner(db)
            db.execute('BEGIN')
            with ZipFile(output,'w',ZIP_DEFLATED) as archive:
                for table in ('readings','observations','edits','reviews','batches','tasks'):
                    cursor = db.execute('SELECT * FROM '+table)
                    text = io.StringIO()
                    writer = csv.writer(text)
                    writer.writerow([c[0] for c in cursor.description])
                    for row in cursor:
                        # Prevent spreadsheet formula execution in free-text notes.
                        writer.writerow(["'"+v if isinstance(v,str) and v[:1] in ('=','+','-','@','\t','\r') else v for v in row])
                    archive.writestr(table+'.csv',text.getvalue().encode('utf-8-sig'))
        output.seek(0)
        return output

    def query(self, category, period, selected, compare):
        try:
            ranges = query_ranges(category,period,selected,compare)
        except ValueError as exc:
            raise HomeError(str(exc)) from None
        # Demo data is explicitly synthetic and stored in a separate database.
        if self.home.demo:
            for _,start,end in ranges:
                with self.db() as db:
                    count = db.execute('SELECT COUNT(*) FROM readings WHERE category=? AND day BETWEEN ? AND ?',
                                       (category,start.isoformat(),end.isoformat())).fetchone()[0]
                if count < (end-start).days+1:
                    self.fetch(category,start,end,'demo')
        result = {'category':category,'name':CATEGORIES[category],'period':period,'selected':selected,
                  'demo':self.home.demo,'source':'archive','updated':time.time()}
        with self.db() as db:
            self.check_owner(db)
            for label,start,end in ranges:
                readings = db.execute('SELECT * FROM readings WHERE category=? AND day BETWEEN ? AND ? ORDER BY day',
                                       (category,start.isoformat(),end.isoformat())).fetchall()
                pending = {r[0] for r in db.execute("SELECT day FROM reviews WHERE category=? AND status='pending' AND day BETWEEN ? AND ?",(category,start.isoformat(),end.isoformat()))}
                buckets = defaultdict(list)
                for item in readings:
                    buckets[item['day'] if period=='Day' else item['day'][:7]].append(item)
                points = []
                for key, items in sorted(buckets.items()):
                    units = {item['unit'] for item in items}
                    unit = next(iter(units)) if len(units)==1 else None
                    if period=='Day':
                        expected = 1
                    else:
                        first = date.fromisoformat(key+'-01')
                        last = min(end,date(first.year,first.month,calendar.monthrange(first.year,first.month)[1]))
                        expected = (last-first).days+1
                    value = float(sum(Decimal(item['amount']) for item in items)) if unit else None
                    points.append({'date':key,'amount':value,'unit':unit,'days':len(items),'expected_days':expected,
                                   'partial':len(items)<expected or not unit,
                                   'pending':any(item['day'] in pending for item in items)})
                units = {p['unit'] for p in points}
                unit = next(iter(units)) if len(units)==1 else None
                total = float(sum(Decimal(str(p['amount'])) for p in points)) if points and unit else None
                result[label] = {'start':start.isoformat(),'end':end.isoformat(),'points':points,'unit':unit,'total':total,
                    'stored_days':len(readings),'expected_days':(end-start).days+1,'pending_days':len(pending),
                    'partial':len(readings)<(end-start).days+1 or not unit}
        return result

    def history(self, category, day):
        self.valid_key(category,day)
        with self.db() as db:
            self.check_owner(db)
            current = db.execute('SELECT * FROM readings WHERE category=? AND day=?',(category,day)).fetchone()
            return {'current':dict(current) if current else None,
                'observations':[dict(r) for r in db.execute('SELECT * FROM observations WHERE category=? AND day=? ORDER BY id DESC LIMIT 100',(category,day))],
                'edits':[dict(r) for r in db.execute('SELECT * FROM edits WHERE category=? AND day=? ORDER BY id DESC LIMIT 100',(category,day))]}

    def reviews(self):
        with self.db() as db:
            self.check_owner(db)
            return [dict(r) for r in db.execute("SELECT r.*,o.amount candidate,o.unit candidate_unit,o.valid,o.raw,o.observed FROM reviews r JOIN observations o ON o.id=r.observation WHERE r.status='pending' ORDER BY r.id DESC LIMIT 200")]

    @staticmethod
    def note(payload):
        note = payload.get('reason')
        if not isinstance(note,str) or not 1 <= len(note.strip()) <= 500:
            raise HomeError('수정·검토 사유를 1~500자로 입력하세요.')
        return note.strip()

    def edit(self, payload):
        category, day = payload.get('category'), payload.get('day')
        self.valid_key(category,day)
        reason = self.note(payload)
        try:
            value = amount(payload.get('amount'))
        except ValueError as exc:
            raise HomeError(str(exc)) from None
        unit, version = payload.get('unit'), payload.get('version')
        if not isinstance(unit,str) or not unit.strip() or len(unit)>24 or type(version) is not int:
            raise HomeError('단위와 수정 버전을 확인하세요.')
        with self.db() as db:
            self.check_owner(db,bind=True)
            old = db.execute('SELECT * FROM readings WHERE category=? AND day=?',(category,day)).fetchone()
            if version != (old['version'] if old else 0):
                raise HomeError('다른 수집 또는 수정으로 값이 바뀌었습니다. 이력을 다시 조회하세요.',409)
            self.write(db,category,day,value,unit,'manual',reason,old,locked=True)
        return {'message':'수정 이력을 저장했습니다. 이후 수집은 이 값을 자동으로 덮어쓰지 않습니다.'}

    def resolve(self,payload):
        ident, action = payload.get('id'),payload.get('action')
        reason = self.note(payload)
        if type(ident) is not int or action not in ('accept','keep'):
            raise HomeError('검토 항목과 선택을 확인하세요.')
        with self.db() as db:
            self.check_owner(db)
            review = db.execute("SELECT r.*,o.amount candidate,o.unit candidate_unit,o.valid,o.raw FROM reviews r JOIN observations o ON o.id=r.observation WHERE r.id=? AND r.status='pending'",(ident,)).fetchone()
            if not review:
                raise HomeError('이미 처리했거나 없는 검토 항목입니다.',409)
            if action=='accept':
                self.valid_key(review['category'],review['day'])
                if not review['valid'] or review['candidate'] is None:
                    raise HomeError('잘못된 원본은 채택할 수 없습니다. 직접 수정하거나 기존 값을 유지하세요.')
                old = db.execute('SELECT * FROM readings WHERE category=? AND day=?',(review['category'],review['day'])).fetchone()
                already_current = old and old['unit']==review['candidate_unit'] and Decimal(old['amount'])==Decimal(review['candidate'])
                if not already_current and (old['version'] if old else 0) != review['base_version']:
                    raise HomeError('검토 이후 확정값이 바뀌었습니다. 이력을 확인하고 직접 수정하세요.',409)
                if not already_current:
                    self.write(db,review['category'],review['day'],review['candidate'],review['candidate_unit'],'review',reason,old,locked=bool(old and old['locked']))
            matching = [r['id'] for r in self.related_reviews(db,review['category'],review['day'],'pending') if self.same_candidate(r,review)]
            db.executemany('UPDATE reviews SET status=?,note=?,resolved=? WHERE id=?',[(action,reason,time.time(),item) for item in matching])
        return {'message':f'검토 결과를 저장했습니다. 같은 데이터 {len(matching)}건을 함께 처리했습니다.', 'resolved_ids':matching}
