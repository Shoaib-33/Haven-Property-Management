import os
import unittest
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from app.clock import local_today


class ClockTests(unittest.TestCase):
    def test_today_follows_the_configured_timezone(self):
        for name in ('Pacific/Kiritimati', 'Pacific/Pago_Pago', 'Europe/Amsterdam'):
            with mock.patch.dict(os.environ, {'HAVEN_TIMEZONE': name}):
                self.assertEqual(local_today(), datetime.now(ZoneInfo(name)).date())

    def test_unknown_or_empty_timezone_falls_back_to_the_server_date(self):
        with mock.patch.dict(os.environ, {'HAVEN_TIMEZONE': 'Not/AZone'}):
            self.assertEqual(local_today(), datetime.now().date())
        with mock.patch.dict(os.environ, {'HAVEN_TIMEZONE': ''}):
            self.assertEqual(local_today(), datetime.now().date())


if __name__ == '__main__':
    unittest.main()
