"""Synthetic events only: these fixtures are not captured phone usage."""
import datetime as dt
import importlib
import unittest


def core():
    try:
        return importlib.import_module('grapheneos_screen_time.core')
    except ModuleNotFoundError as exc:
        raise AssertionError('Reusable report engine has not been implemented') from exc


def event(when, kind, package='android', instance=''):
    suffix = f' instanceId={instance}' if instance else ''
    return f'time={int(when.timestamp()*1000)} type={kind} package={package}{suffix}'


def sample(days=7):
    zone = dt.timezone(dt.timedelta(hours=2))
    end = dt.datetime(2025, 1, 15, tzinfo=zone)
    start = end - dt.timedelta(days=days)
    events = [event(start-dt.timedelta(hours=12), 'SCREEN_NON_INTERACTIVE')]
    for i in range(days):
        when = start + dt.timedelta(days=i, hours=12)
        events.extend([
            event(when, 'SCREEN_INTERACTIVE'), event(when, 'KEYGUARD_HIDDEN'),
            event(when, 'ACTIVITY_RESUMED', 'example.app', '1'),
            event(when+dt.timedelta(minutes=5), 'ACTIVITY_PAUSED', 'example.app', '1'),
            event(when+dt.timedelta(minutes=10), 'SCREEN_NON_INTERACTIVE'),
            event(when+dt.timedelta(minutes=10), 'KEYGUARD_SHOWN'),
        ])
    events.extend([
        event(end+dt.timedelta(hours=10), 'SCREEN_INTERACTIVE'),
        event(end+dt.timedelta(hours=10), 'KEYGUARD_HIDDEN'),
        event(end+dt.timedelta(hours=10), 'ACTIVITY_RESUMED', 'example.app', '1'),
        event(end+dt.timedelta(hours=10, minutes=3), 'ACTIVITY_PAUSED', 'example.app', '1'),
        event(end+dt.timedelta(hours=10, minutes=4), 'SCREEN_NON_INTERACTIVE'),
    ])
    return '\n'.join(events)


def report(raw=None, **kwargs):
    return core().build_report(sample() if raw is None else raw, '',
        'package:example.app', 'package:example.app', ' example.app/.Main',
        '2025-01-15T12:00:00+0200', **kwargs)


class ReportTests(unittest.TestCase):
    def test_complete_calendar_days_screen_time_independent_of_app_sum(self):
        data = report()
        self.assertEqual(data['period_days'], 7)
        self.assertEqual(data['screen_on_ms'], 7*10*60000)
        self.assertEqual(data['unlocked_ms'], 7*10*60000)
        self.assertEqual(data['app_rows'][0]['week_ms_estimate'], 7*5*60000)
        self.assertEqual(data['app_rows'][0]['avg_ms_estimate'], 5*60000)
        self.assertEqual(data['app_rows'][0]['days'], 7)
        self.assertEqual(data['today_screen']['screen_on_ms'], 4*60000)
        self.assertEqual(data['app_rows'][0]['daily_ms'][-1], 3*60000)
        self.assertNotIn('serial', data)

    def test_short_history_uses_only_available_complete_days(self):
        data = report(sample(days=3))
        self.assertEqual(data['period_days'], 3)
        self.assertEqual(len(data['screen_days']), 3)
        self.assertEqual(len(data['app_rows'][0]['daily_ms']), 4)
        self.assertEqual(data['app_rows'][0]['avg_ms_estimate'], 5*60000)
        self.assertTrue(data['warnings'])

    def test_same_millisecond_resume_pause_does_not_create_phantom_usage(self):
        begin = dt.datetime.fromisoformat('2025-01-14T10:00:00+02:00')
        raw = '\n'.join([
            event(begin-dt.timedelta(days=7), 'SCREEN_NON_INTERACTIVE'),
            event(begin, 'SCREEN_INTERACTIVE'), event(begin, 'KEYGUARD_HIDDEN'),
            event(begin, 'ACTIVITY_RESUMED', 'example.app', '1'),
            event(begin, 'ACTIVITY_PAUSED', 'example.app', '1'),
            event(begin+dt.timedelta(hours=1), 'SCREEN_NON_INTERACTIVE'),
        ])
        data = core().build_report(raw, raw, 'package:example.app', 'package:example.app',
                                   ' example.app/.Main', '2025-01-15T12:00:00+0200')
        self.assertEqual(data['app_rows'][0]['week_ms_estimate'], 0)
        self.assertEqual(data['app_rows'][0]['days'], 0)
        self.assertEqual(data['screen_on_ms'], 60*60000)
        self.assertEqual(sum(day['unlocks'] for day in data['screen_days']), 1)

    def test_overlapping_dumps_do_not_double_count_usage_or_unlocks(self):
        raw = sample()
        data = core().build_report(raw+'\n'+raw, '\n'.join(reversed(raw.splitlines())),
                                   'package:example.app', 'package:example.app',
                                   ' example.app/.Main', '2025-01-15T12:00:00+0200')
        self.assertEqual(data, report(raw))
        self.assertEqual(sum(day['unlocks'] for day in data['screen_days']), 7)
        self.assertEqual(data['today_screen']['unlocks'], 1)

    def test_same_millisecond_pause_resume_keeps_today_activity_open(self):
        begin = dt.datetime.fromisoformat('2025-01-15T10:00:00+02:00')
        raw = '\n'.join([
            event(begin-dt.timedelta(days=8), 'SCREEN_NON_INTERACTIVE'),
            event(begin, 'SCREEN_INTERACTIVE'),
            event(begin, 'ACTIVITY_PAUSED', 'example.app', '1'),
            event(begin, 'ACTIVITY_RESUMED', 'example.app', '1'),
            event(begin+dt.timedelta(hours=1), 'SCREEN_NON_INTERACTIVE'),
        ])
        data = report(raw)
        self.assertEqual(data['app_rows'][0]['week_ms_estimate'], 0)
        self.assertEqual(data['app_rows'][0]['daily_ms'][-1], 60*60000)
        self.assertEqual(data['today_screen']['screen_on_ms'], 60*60000)

    def test_daylight_saving_midnights_split_real_elapsed_time(self):
        midnights = [f'2025-03-{day:02}T00:00:00+01:00' for day in range(24, 31)]
        midnights.append('2025-03-31T00:00:00+02:00')
        begin = dt.datetime.fromisoformat('2025-03-29T23:30:00+01:00')
        finish = dt.datetime.fromisoformat('2025-03-30T03:30:00+02:00')
        raw = '\n'.join([
            event(dt.datetime.fromisoformat('2025-03-23T12:00:00+01:00'), 'SCREEN_NON_INTERACTIVE'),
            event(begin, 'SCREEN_INTERACTIVE'), event(begin, 'KEYGUARD_HIDDEN'),
            event(begin, 'ACTIVITY_RESUMED', 'example.app', '1'),
            event(finish, 'ACTIVITY_PAUSED', 'example.app', '1'),
            event(finish, 'SCREEN_NON_INTERACTIVE'),
        ])
        data = core().build_report(raw, '', 'package:example.app', 'package:example.app',
            ' example.app/.Main', '2025-03-31T12:00:00+0200', calendar_midnights=midnights)
        self.assertEqual(data['screen_on_ms'], 3*60*60000)
        self.assertEqual(data['screen_days'][5]['screen_on_ms'], 30*60000)
        self.assertEqual(data['screen_days'][5]['unlocks'], 1)
        self.assertEqual(data['screen_days'][6]['unlocks'], 0)
        self.assertEqual(data['screen_days'][6]['screen_on_ms'], 150*60000)
        self.assertEqual(data['app_rows'][0]['days'], 2)


if __name__ == '__main__':
    unittest.main()
