import unittest
from hedging_tools.market_time import fresh_age
from hedging_tools.scanner import screen
from tests.test_scanner import fixture, CONFIG, NOW


class MarketTimeTests(unittest.TestCase):
    def test_bounded_skew_and_unchanged_staleness(self):
        for age in (-1,-.15,0,30):
            self.assertTrue(fresh_age(age,30))
        for age in (-1.001,30.001,float('nan'),float('inf')):
            self.assertFalse(fresh_age(age,30))

    def test_small_exchange_clock_lead_does_not_reject_snapshot(self):
        data=fixture()
        for field in ('ticker','interest','funding'):
            data[field]['ts']=str(int(NOW.timestamp()*1000)+150)
        self.assertEqual(screen(data,CONFIG,NOW)['status'],'WATCHLIST')

    def test_large_lead_and_stale_data_still_fail_with_age(self):
        for field,offset in (('interest',1001),('ticker',-30001),('interest',-300001)):
            data=fixture();data[field]['ts']=str(int(NOW.timestamp()*1000)+offset)
            result=screen(data,CONFIG,NOW)
            self.assertEqual(result['status'],'DATA_ERROR')
            self.assertIn('age=',result['reasons'][0])
