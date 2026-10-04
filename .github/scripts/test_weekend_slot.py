import unittest
from datetime import datetime, timedelta, timezone
from weekend_slot import scheduled_phase, LONDON
class Slots(unittest.TestCase):
    def check_slot(self, start):
        slots={'ci':(start.strftime('%a %H:%M'),150,'ci.yml')}
        utc=start.astimezone(timezone.utc);cron=f'{utc.minute} {utc.hour} * * {(utc.weekday()+1)%7}'
        self.assertEqual(scheduled_phase(start,cron,slots),('ci','ci.yml'))
        self.assertEqual(scheduled_phase(start+timedelta(minutes=67),cron,slots),('ci','ci.yml'))
        self.assertIsNone(scheduled_phase(start+timedelta(minutes=150),cron,slots))
        wrong=(utc-timedelta(hours=1));other=f'{wrong.minute} {wrong.hour} * * {(wrong.weekday()+1)%7}'
        self.assertIsNone(scheduled_phase(start+timedelta(hours=1),other,slots))
    def test_gmt_window(self):self.check_slot(datetime(2027,1,8,22,30,tzinfo=LONDON))
    def test_bst_window(self):self.check_slot(datetime(2026,10,2,22,30,tzinfo=LONDON))
    def test_london_midnight_utc_previous_day(self):self.check_slot(datetime(2026,10,4,0,0,tzinfo=LONDON))
    def test_spring_transition(self):self.check_slot(datetime(2027,3,28,2,0,tzinfo=LONDON))
    def test_autumn_transition(self):self.check_slot(datetime(2026,10,25,2,0,tzinfo=LONDON))
    def test_council_crosses_into_monday(self):self.check_slot(datetime(2026,10,4,23,30,tzinfo=LONDON))
    def test_dast_disabled(self):self.assertIsNone(scheduled_phase(datetime.now(timezone.utc),'0 0 * * 0',{'dast':('N/A',120,'dast.yml')}))
if __name__=='__main__':unittest.main()
