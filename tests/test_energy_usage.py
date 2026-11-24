from datetime import date, datetime, timezone
from unittest.mock import Mock

import pytest

from energy_usage import query_ranges, normalize_series, reading_date
from home_service import HomeError, HomeService
from webapp import create_app


def test_ranges_whole_previous_period_and_leap_year():
    ranges = query_ranges('Water', 'Day', '2024-02', '1', today=date(2024, 2, 29))
    assert ranges == [('current', date(2024, 2, 1), date(2024, 2, 29)),
                      ('previous', date(2023, 2, 1), date(2023, 2, 28))]
    ranges = query_ranges('Gas', 'Month', '2026', '1', today=date(2026, 11, 23))
    assert ranges[0][1:] == (date(2026, 1, 1), date(2026, 11, 23))
    assert ranges[1][1:] == (date(2025, 1, 1), date(2025, 12, 31))
    ranges = query_ranges('Electricity', 'Day', '2026-11', '1', today=date(2026, 11, 23))
    assert ranges == [('current', date(2026, 11, 1), date(2026, 11, 23)),
                      ('previous', date(2025, 11, 1), date(2025, 11, 30))]
    ranges = query_ranges('Gas', 'Day', '2025-02', '1', today=date(2025, 2, 12))
    assert ranges[1][1:] == (date(2024, 2, 1), date(2024, 2, 29))


def test_month_end_readings_and_december_not_filtered_from_comparison():
    home = HomeService()
    home.api = Mock(complex_id='C', access_token='A', refresh_token='R')
    home.api._send_request.side_effect = [
        {'Resources': [row('2026-11-01')]},
        {'Resources': [row('2025-11-30', '558.2')]}]
    data = home.energy_usage('Electricity', 'Day', '2026-11', '1')
    assert data['previous']['total'] == 558.2
    assert home.api._send_request.call_args.args[1]['End_Time'] == '2025-11-30T23:59:59'
    home.api._send_request.side_effect = [
        {'Resources': [row('2026-11-01')]}, {'Resources': [row('2025-12-01', '100')]}]
    data = home.energy_usage('Electricity', 'Month', '2026', '1')
    assert data['previous']['points'][0]['date'] == '2025-12'
    assert home.api._send_request.call_args.args[1]['End_Time'] == '2025-12-31T23:59:59'


@pytest.mark.parametrize('args', [('Other','Day','2026-01','0'), ('Gas','Year','2026','0'),
                                  ('Gas','Day','2026-13','0'), ('Gas','Day','2027-01','0'),
                                  ('Gas','Day','2026-1','0'), ('Gas','Month','2013','0'),
                                  ('Gas','Month','2026','yes')])
def test_invalid_queries(args):
    with pytest.raises(ValueError):
        query_ranges(*args, today=date(2026, 11, 23))


def row(day='2026-01-01', amount='1.25', unit='kWh'):
    return {'Start_Time': day, 'Amount': amount, 'Unit': unit, 'Access_Token': 'NEVER_RETURN'}


def normalize(rows):
    return normalize_series({'Resources': rows}, date(2026, 1, 1), date(2026, 1, 31), 'Day')


def test_readings_zero_missing_units_and_allowlist():
    data = normalize([row(), row('2026-01-02', '0')])
    assert data['total'] == 1.25
    assert data['points'][1]['amount'] == 0
    assert 'NEVER_RETURN' not in str(data)
    assert normalize([])['total'] is None
    assert normalize([row(amount=None)])['total'] is None
    assert normalize([row(), row('2026-01-02', unit='Wh')])['total'] is None


def test_diagnostics_distinguish_empty_outside_and_missing_without_leaking_secrets():
    outside = normalize([row(day='2025-12-31')])
    assert outside['points'] == []
    assert outside['diagnostics']['received'] == 1
    assert outside['diagnostics']['in_range'] == 0
    assert outside['diagnostics']['response_start'] == '2025-12-31'
    assert normalize([])['diagnostics']['received'] == 0
    missing = normalize([row(amount=None)])
    assert missing['diagnostics']['missing_amount'] == 1
    assert 'NEVER_RETURN' not in str(outside) + str(missing)


@pytest.mark.parametrize('rows', [[row(), row()], [row(amount='NaN')], [row(amount='Infinity')],
                                  [row(amount='-1')], [row(amount=True)], [row(day='bad')], ['bad']])
def test_bad_remote_readings(rows):
    with pytest.raises(ValueError):
        normalize(rows)


def test_sum_overflow_not_returned_as_infinity():
    with pytest.raises(ValueError):
        normalize([row(amount='1e308'), row('2026-01-02', '1e308')])


@pytest.mark.parametrize('as_string', [True, False])
def test_app_milliseconds_korea_midnight_and_month_boundary(as_string):
    # UTC Dec 31 15:00 is Jan 1 midnight in Korea.
    epoch = int(datetime(2025, 12, 31, 15, tzinfo=timezone.utc).timestamp() * 1000)
    raw = str(epoch) if as_string else epoch
    result = normalize([row(day=raw)])
    assert result['points'] == [{'date': '2026-01-01', 'amount': 1.25, 'unit': 'kWh'}]
    assert reading_date(str(epoch - 1)) == date(2025, 12, 31)
    result = normalize_series({'Resources': [row(day=raw)]}, date(2026, 1, 1), date(2026, 12, 31), 'Month')
    assert result['points'][0]['date'] == '2026-01'


@pytest.mark.parametrize('raw', [None, True, 1e99, '12345678901', 'bad', 10**30])
def test_invalid_timestamp_does_not_fall_back_to_today(raw):
    with pytest.raises(ValueError):
        reading_date(raw)


@pytest.mark.parametrize('as_string', [True, False])
def test_actual_nas_seconds_response_retains_all_23_days(as_string):
    readings = []
    for offset in range(23):
        timestamp = 1793458800 + offset * 86400
        readings.append(row(day=str(timestamp) if as_string else timestamp))
    result = normalize_series({'Resources': readings}, date(2026, 11, 1), date(2026, 11, 23), 'Day')
    assert result['diagnostics']['received'] == result['diagnostics']['in_range'] == 23
    assert result['points'][0]['date'] == '2026-11-01'
    assert result['points'][-1]['date'] == '2026-11-23'
    assert result['total'] == 28.75
    assert reading_date(1793458799) == date(2026, 10, 31)
    assert reading_date(1793458800000) == reading_date('1793458800')


def test_existing_auth_reused_without_login_or_writes():
    home = HomeService()
    home.api = Mock(complex_id='COMPLEX', access_token='ACCESS', refresh_token='REFRESH')
    home.api._send_request.return_value = {'Resources': [row()]}
    result = home.energy_usage('Electricity', 'Day', '2026-01', '1')
    assert result['current']['total'] == 1.25
    assert len(home.api._send_request.call_args_list) == 2
    for call in home.api._send_request.call_args_list:
        endpoint, fields = call.args
        assert endpoint == '/used/list'
        assert fields['Query_Type'] == 'Day' and fields['Category_Type'] == 'Electricity'
        assert fields['Access_Token'] == 'ACCESS'
        assert fields['Refresh_Token'] == 'REFRESH'
    assert not home.api.get_token.called and not home.api.login.called
    assert 'ACCESS' not in str(result) and 'REFRESH' not in str(result)
    home.restore_connection = Mock()
    home.api._send_request.side_effect = HomeError('rejected', 502, remote_rejected=True)
    with pytest.raises(HomeError):
        home.energy_usage('Gas', 'Month', '2025')
    home.restore_connection.assert_not_called()


def test_usage_requires_connection():
    with pytest.raises(HomeError, match='연결'):
        HomeService().energy_usage('Gas', 'Month', '2025')


def test_usage_admin_api_and_demo(tmp_path):
    app = create_app({'TESTING': True, 'DEMO': False, 'ADMIN_PIN': '012345',
                      'SECRET_KEY': 'test', 'CONFIG_DIR': str(tmp_path),
                      'DATA_DIR': str(tmp_path), 'START_SCHEDULER': False,
                      'SESSION_COOKIE_SECURE': False}, service=HomeService(demo=True))
    try:
        client = app.test_client()
        url = '/api/usage?category=Gas&period=Month&selected=2025&compare=1'
        assert client.get(url).status_code == 401
        csrf = client.get('/api/session').json['csrf']
        assert client.post('/api/login', json={'pin': '012345'}, headers={'X-CSRF-Token': csrf}).status_code == 200
        result = client.get(url)
        assert result.status_code == 200 and result.json['demo'] is True
        assert len(result.json['current']['points']) == 12
        assert len(result.json['previous']['points']) == 12
        assert client.get('/api/usage?category=Bad&period=Month&selected=2025').status_code == 400
        csrf = client.get('/api/session').json['csrf']
        assert client.post('/api/usage', json={}, headers={'X-CSRF-Token': csrf}).status_code == 405
    finally:
        app.extensions['jobs'].close()
