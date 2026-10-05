"""Synthetic ADB discovery and read-only collector tests, not phone results."""
import importlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from test_core import sample


def adb_module():
    try:
        return importlib.import_module('grapheneos_screen_time.adb')
    except ModuleNotFoundError as exc:
        raise AssertionError('Portable read-only ADB collector is not implemented') from exc


class AdbTests(unittest.TestCase):
    def test_discovery_selected_device_and_only_read_commands(self):
        m=adb_module()
        self.assertEqual(m.find_adb(sys.executable),str(Path(sys.executable).resolve()))
        self.assertEqual(m.select_device('List of devices attached\nexample-device device product:sample\n'),'example-device')
        for text, expected in [
            ('List of devices attached\n','подключ'),
            ('List of devices attached\nexample-device unauthorized\n','разреш'),
            ('List of devices attached\nexample-device offline\n','offline'),
            ('List of devices attached\nfirst device\nsecond device\n','несколько'),
        ]:
            with self.assertRaisesRegex(ValueError,expected):
                m.select_device(text)
        self.assertEqual(m.select_device('first device\nsecond device\n','second'),'second')
        calls=[]
        def read_only(client,*args):
            calls.append(args)
            if args==('devices','-l'):
                return 'List of devices attached\nexample-device device\n'
            if args==('shell','am','get-current-user'):
                return '0\n'
            if args==('shell','getprop','ro.product.model'):
                return 'Synthetic Test Device\n'
            if args==('shell','date','+%Y-%m-%dT%H:%M:%S%z'):
                return '2025-01-15T12:00:00+0200\n'
            if args[:3]==('shell','date','-d'):
                return args[3]+'+0200\n'
            if args==('shell','dumpsys','usagestats','file','0','daily'):
                return sample()
            if args==('shell','dumpsys','usagestats','-c'):
                return ''
            if args[:4]==('shell','pm','list','packages'):
                return 'package:example.app\n'
            if args[:4]==('shell','cmd','package','query-activities'):
                return ' example.app/.Main\n'
            raise AssertionError(f'Unexpected non-read-only command: {args}')
        from grapheneos_screen_time.server import App
        with tempfile.TemporaryDirectory() as temp, patch.object(m.Adb,'run',read_only):
            app=App(Path(temp),adb=sys.executable)
            report=m.read_phone(app)
        self.assertEqual(report['phone_model'],'Synthetic Test Device')
        self.assertEqual(report['period_days'],7)
        self.assertNotIn('serial',report)
        self.assertEqual(len([c for c in calls if c[:3]==('shell','date','-d')]),8)


if __name__=='__main__':
    unittest.main()
