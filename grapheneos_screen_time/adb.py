"""Discover installed ADB; run fixed read-only queries against an authorized phone."""
import datetime as dt
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

from .core import build_report

# General public app labels, not a captured device's inventory. Other apps keep
# their package IDs. No APK downloads or launches are needed to collect usage.
KNOWN_APPS = {
    'org.telegram.messenger': ('Telegram','Общение'),
    'org.thoughtcrime.securesms': ('Signal','Общение'),
    'com.whatsapp': ('WhatsApp','Общение'),
    'com.instagram.android': ('Instagram','Соцсети'),
    'com.instagram.barcelona': ('Threads','Соцсети'),
    'com.reddit.frontpage': ('Reddit','Соцсети'),
    'com.twitter.android': ('X','Соцсети'),
    'com.zhiliaoapp.musically': ('TikTok','Соцсети'),
    'com.google.android.youtube': ('YouTube','Видео'),
    'com.netflix.mediaclient': ('Netflix','Видео'),
    'com.openai.chatgpt': ('ChatGPT','ИИ'),
    'ai.perplexity.app.android': ('Perplexity','ИИ'),
    'com.anthropic.claude': ('Claude','ИИ'),
    'com.android.chrome': ('Chrome','Браузеры'),
    'org.mozilla.firefox': ('Firefox','Браузеры'),
    'app.vanadium.browser': ('Vanadium','Браузеры'),
    'com.brave.browser': ('Brave','Браузеры'),
    'com.google.android.apps.maps': ('Google Maps','Карты'),
    'com.google.android.apps.photos': ('Google Photos','Фото'),
    'com.google.android.GoogleCamera': ('Google Camera','Фото'),
    'com.google.android.gm': ('Gmail','Почта'),
    'com.google.android.calendar': ('Google Calendar','Планирование'),
    'com.google.android.apps.docs': ('Google Drive','Документы'),
    'com.spotify.music': ('Spotify','Музыка'),
    'com.android.dialer': ('Телефон','Общение'),
    'com.google.android.dialer': ('Телефон','Общение'),
}


def find_adb(explicit=None):
    value = explicit or os.environ.get('ADB_PATH')
    if value:
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            raise ValueError('Указанный ADB не найден. Проверьте --adb или ADB_PATH.')
        return str(path)
    found = shutil.which('adb')
    if found:
        return found
    name = 'adb.exe' if sys.platform == 'win32' else 'adb'
    candidates = [Path.home()/'platform-tools'/name, Path.home()/'Android'/'Sdk'/'platform-tools'/name]
    for key in ('ANDROID_HOME','ANDROID_SDK_ROOT'):
        if os.environ.get(key):
            candidates.append(Path(os.environ[key])/'platform-tools'/name)
    if sys.platform == 'win32' and os.environ.get('LOCALAPPDATA'):
        candidates.append(Path(os.environ['LOCALAPPDATA'])/'Android'/'Sdk'/'platform-tools'/name)
    for path in candidates:
        if path.is_file():
            return str(path.resolve())
    raise ValueError('ADB не найден. Установите официальные SDK Platform-Tools и добавьте в PATH или укажите --adb. Программа ничего не устанавливает на телефон.')


def select_device(text, requested=None):
    devices = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] in {'device','unauthorized','offline','no'}:
            devices[parts[0]] = parts[1]
    if requested:
        state = devices.get(requested)
        if state == 'device':
            return requested
        if state == 'unauthorized':
            raise ValueError('Разблокируйте телефон и разрешите отладку USB для этого компьютера.')
        raise ValueError('Выбранный телефон не подключён или offline.')
    online = [serial for serial,state in devices.items() if state == 'device']
    if len(online) == 1:
        return online[0]
    if len(online) > 1:
        raise ValueError('Подключены несколько устройств. Отключите лишние или запустите с --serial.')
    if 'unauthorized' in devices.values():
        raise ValueError('Разблокируйте телефон и разрешите отладку USB для этого компьютера.')
    if 'offline' in devices.values():
        raise ValueError('Телефон offline. Переподключите кабель и разблокируйте телефон.')
    raise ValueError('Телефон не подключён. Подключите USB-кабель с передачей данных и включите USB-отладку.')


class Adb:
    def __init__(self, path, serial=None):
        self.path, self.serial = path, serial

    def run(self, *args):
        command = [self.path]+(['-s',self.serial] if self.serial else [])+list(args)
        result = subprocess.run(command,capture_output=True,encoding='utf-8',errors='replace',timeout=90,
                                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode:
            if 'unauthorized' in result.stderr.lower():
                raise ValueError('Разблокируйте телефон и разрешите отладку USB.')
            raise ValueError('ADB не смог прочитать данные. Проверьте подключение, разрешение USB-отладки и доступность профиля.')
        return result.stdout


def read_phone(app):
    path = find_adb(app.adb)
    devices = Adb(path).run('devices','-l')
    serial = select_device(devices, app.serial)
    adb = Adb(path,serial)
    user = str(app.user)
    if adb.run('shell','am','get-current-user').strip() != user:
        raise ValueError('На телефоне открыт другой профиль. Переключитесь в выбранный профиль или укажите --user.')
    daily = adb.run('shell','dumpsys','usagestats','file',user,'daily')
    compact = adb.run('shell','dumpsys','usagestats','-c')
    installed = adb.run('shell','pm','list','packages','--user',user)
    third = adb.run('shell','pm','list','packages','-3','--user',user)
    launch = adb.run('shell','cmd','package','query-activities','--brief','--user',user,'-a','android.intent.action.MAIN','-c','android.intent.category.LAUNCHER')
    model = adb.run('shell','getprop','ro.product.model').strip() or 'Android'
    date = adb.run('shell','date','+%Y-%m-%dT%H:%M:%S%z').strip()
    now = dt.datetime.strptime(date,'%Y-%m-%dT%H:%M:%S%z')
    # Ask the phone for each midnight so DST changes do not assume 24-hour days.
    midnights = []
    for offset in range(7,-1,-1):
        day = (now.date()-dt.timedelta(days=offset)).isoformat()+'T00:00:00'
        midnights.append(adb.run('shell','date','-d',day,'+%Y-%m-%dT%H:%M:%S%z').strip())
    key = hashlib.sha256((serial+':'+user).encode('utf-8')).hexdigest()
    previous = app.load()
    if previous and previous.get('device_key') != key:
        previous = None
    report = build_report(daily,compact,installed,third,launch,date,previous,
                          user=app.user,phone_model=model,calendar_midnights=midnights)
    report['device_key'] = key
    for row in report['app_rows']:
        if row['name'] == row['package'] and row['package'] in KNOWN_APPS:
            row['name'],row['category'] = KNOWN_APPS[row['package']]
    report['app_rows'].sort(key=lambda row:(-row['week_ms_estimate'],-row['days'],row['name']))
    return report
