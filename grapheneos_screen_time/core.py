"""Calendar estimates from read-only Android usage events (no device I/O)."""
from collections import defaultdict
import datetime as dt
import re


def union_intervals(spans, lo, hi):
    merged = []
    for a, b in sorted((max(a, lo), min(b, hi)) for a, b in spans if a < hi and b > lo):
        if b <= a:
            continue
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    return merged


def total_ms(spans, lo, hi):
    return sum(b-a for a, b in union_intervals(spans, lo, hi))


def user_section(text, user=0):
    parts = re.split(r'^\s*user=(\d+)\s*$', text, flags=re.M)
    if len(parts) == 1:
        return text
    for i in range(1, len(parts), 2):
        if parts[i] == str(user):
            return parts[i+1]
    return ''


def build_report(raw_daily, compact, installed_text, third_text, launch_text,
                 date_text, previous=None, user=0, phone_model='Android', calendar_midnights=None):
    now = dt.datetime.strptime(date_text.strip(), '%Y-%m-%dT%H:%M:%S%z')
    midnight = dt.datetime.combine(now.date(), dt.time.min, tzinfo=now.tzinfo)
    start = midnight-dt.timedelta(days=7)
    boundaries = [dt.datetime.fromisoformat(s) for s in calendar_midnights] if calendar_midnights else [start+dt.timedelta(days=i) for i in range(8)]
    if len(boundaries) != 8 or boundaries[-1].date() != now.date() or any(
            b.tzinfo is None or b.time() != dt.time.min for b in boundaries) or any(
            b.date()-a.date() != dt.timedelta(days=1) or b.timestamp() <= a.timestamp()
            for a, b in zip(boundaries, boundaries[1:])):
        raise ValueError('Некорректные календарные границы.')
    start, midnight = boundaries[0], boundaries[-1]
    lo, hi = int(start.timestamp()*1000), int(midnight.timestamp()*1000)
    collected_ms = int(now.timestamp()*1000)
    compact = user_section(compact, user)
    # Keep the first source position of each event when the dumps overlap.
    events = {}
    for line in (raw_daily+'\n'+compact).splitlines():
        m = re.search(r'time=(\d+) type=(\S+) package=(\S+)(.*)', line)
        if m:
            instance = re.search(r'instanceId=(\d+)', m[4])
            t = int(m[1])
            if t <= collected_ms:
                events[(t, m[2], m[3], instance[1] if instance else '')] = None
    events = sorted(events, key=lambda event: event[0])
    if not events:
        raise ValueError('Нет истории использования. Прежние данные сохранены.')
    warnings = []
    if events[0][0] > lo:
        boundaries = [b for b in boundaries if int(b.timestamp()*1000) >= events[0][0]]
        if not boundaries:
            boundaries = [midnight]
        start = boundaries[0]
        lo = int(start.timestamp()*1000)
        warnings.append('История короче недели: средние рассчитаны только по доступным полным дням.')
    period_days = len(boundaries)-1
    windows = [(int(a.timestamp()*1000), int(b.timestamp()*1000)) for a,b in zip(boundaries,boundaries[1:])]
    active, spans = {}, defaultdict(list)
    screen, unlocked, unlocks = [], [], []
    on_start = unlocked_start = None
    on = open_lock = False
    for t, kind, package, instance in events:
        key = (package, instance)
        if kind == 'ACTIVITY_RESUMED':
            active.setdefault(key, t)
        elif kind in {'ACTIVITY_PAUSED', 'ACTIVITY_STOPPED'}:
            if key in active:
                spans[package].append((active.pop(key), t))
        if kind in {'DEVICE_SHUTDOWN', 'SCREEN_NON_INTERACTIVE'}:
            for (p, _), begin in active.items():
                spans[p].append((begin, t))
            active = {}
            if on_start is not None:
                screen.append((on_start, t))
            if unlocked_start is not None:
                unlocked.append((unlocked_start, t))
            on_start = unlocked_start = None
            on = False
            if kind == 'DEVICE_SHUTDOWN':
                open_lock = False
        elif kind == 'SCREEN_INTERACTIVE':
            on = True
            if on_start is None:
                on_start = t
            if open_lock and unlocked_start is None:
                unlocked_start = t
        elif kind == 'KEYGUARD_HIDDEN':
            open_lock = True
            if on and unlocked_start is None:
                unlocked_start = t
            unlocks.append(t)
        elif kind == 'KEYGUARD_SHOWN':
            open_lock = False
            if unlocked_start is not None:
                unlocked.append((unlocked_start, t))
            unlocked_start = None
    for (p, _), begin in active.items():
        spans[p].append((begin, collected_ms))
    if on_start is not None:
        screen.append((on_start, collected_ms))
    if unlocked_start is not None:
        unlocked.append((unlocked_start, collected_ms))
    sections, section = {}, None
    for line in compact.splitlines():
        m = re.search(r'In-memory (daily|weekly|monthly|yearly) stats', line)
        if m:
            section = m[1]
            sections[section] = {}
        elif section:
            m = re.search(r'package=(\S+) totalTimeUsed=(\d+) lastTimeUsed=(\d+).*?appLaunchCount=(\d+)', line)
            if m:
                sections[section][m[1]] = {'ms': int(m[2]), 'last': int(m[3]), 'launches': int(m[4])}
            if 'UsageStatsDatabase:' in line:
                section = None
    installed = set(re.findall(r'^package:(\S+)', installed_text, re.M))
    third = set(re.findall(r'^package:(\S+)', third_text, re.M))
    launchable = set(re.findall(r'^\s*([\w.]+)/', launch_text, re.M)) & installed
    visible = (launchable | third)-{'com.google.android.gms'}
    cache = {a['package']: a for a in (previous or {}).get('app_rows', [])}
    rows = []
    for package in visible:
        historic = sections.get('yearly', {}).get(package, {})
        daily = [total_ms(spans[package], a, b) for a,b in windows]
        daily.append(total_ms(spans[package], hi, collected_ms))
        old = cache.get(package, {})
        dates = [boundaries[i].date().isoformat() for i in range(period_days) if daily[i] > 0]
        rows.append({'package': package, 'name': old.get('name', package), 'category': old.get('category', 'Прочее'),
                     'launchable': package in launchable, 'third_party': package in third,
                     'days': len(dates), 'used_dates': dates, 'daily_ms': daily,
                     'week_ms_estimate': sum(daily[:period_days]), 'avg_ms_estimate': sum(daily[:period_days])/period_days if period_days else None,
                     'last_used': dt.datetime.fromtimestamp(historic['last']/1000, now.tzinfo).isoformat() if historic.get('last') else None,
                     'history_launches': historic.get('launches', 0), 'history_ms': historic.get('ms', 0)})
    rows.sort(key=lambda a: (-a['week_ms_estimate'], -a['days'], a['name']))
    day_stats = []
    for i in range(period_days):
        date = boundaries[i].date().isoformat()
        a, b = windows[i]
        day_stats.append({'date': date, 'screen_on_ms': total_ms(screen, a, b),
                          'unlocked_ms': total_ms(unlocked, a, b), 'unlocks': sum(a <= t < b for t in unlocks)})
    today = now.date().isoformat()
    today_screen = {'date': today, 'screen_on_ms': total_ms(screen, hi, collected_ms),
                    'unlocked_ms': total_ms(unlocked, hi, collected_ms), 'unlocks': sum(hi <= t < collected_ms for t in unlocks)}
    return {'period_start': start.isoformat(), 'period_end_exclusive': midnight.isoformat(),
            'period_days': period_days, 'collected_at': date_text.strip(), 'screen_on_ms': total_ms(screen, lo, hi),
            'unlocked_ms': total_ms(unlocked, lo, hi), 'screen_days': day_stats, 'app_rows': rows,
            'today_screen': today_screen, 'technical_package_count': len(installed-visible),
            'phone_model': phone_model, 'user': user, 'warnings': warnings, 'estimated': True}
