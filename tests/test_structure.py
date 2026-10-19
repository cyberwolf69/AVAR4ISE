import unittest
from hedging_tools.risk_engine import number as D
from hedging_tools.structure import pivots, trend, setup
from hedging_tools.scanner import screen
from tests.test_scanner import fixture, CONFIG, NOW


def rows(prices):
    return [(i*900000, D(p)+D('.4'), D(p)-D('.4'), D(p), D(100))
            for i, p in enumerate(prices)]


def sample():
    slow = rows([100,102,105,102,101,103,106,104,103,105,107,106,105])
    fast = rows([100,101,104,102,101,102,103,105,104.5])
    fast[-1] = (fast[-1][0],D('105'),D('104.2'),D('104.8'),D(100))
    # BOS close 105, pullback close 104.5, latest retest confirms recovery.
    fast.insert(-1, (fast[-1][0],D('105'),D('104.3'),D('104.5'),D(100)))
    fast = [(i*900000,*r[1:]) for i,r in enumerate(fast)]
    return fast,slow


class StructureTests(unittest.TestCase):
    def test_pivot_available_only_after_right_bars(self):
        r=rows([100,101,104,102,101])
        self.assertEqual(pivots(r[:4]), [])
        p=pivots(r)[0]
        self.assertEqual((p['index'],p['confirmed']), (2,4))
        r=rows([100,101,104,102,101,103,106,104,102,105,108,106,104])
        for n in range(5,len(r)+1):
            self.assertEqual(pivots(r[:n]),[p for p in pivots(r) if p['confirmed']<n])

    def test_long_and_mirrored_short(self):
        f,s=sample()
        result=setup(f,s,D(2),D('104.8'),D('104.9'))
        self.assertEqual(result.get('action'),'LONG',result)
        self.assertLess(result['stop'],D('104.2'))
        def mirror(r):
            return [(t,D(220)-lo,D(220)-hi,D(220)-c,v) for t,hi,lo,c,v in r]
        result=setup(mirror(f),mirror(s),D(2),D('115.1'),D('115.2'))
        self.assertEqual(result.get('action'),'SHORT',result)
        self.assertGreater(result['stop'],D('115.8'))

    def test_bos_candle_is_not_retest(self):
        f,s=sample()
        self.assertIn('WAIT_RETEST',setup(f[:8],s,D(2),D(105),D('105.1'))['reason'])

    def test_invalidated_retest_and_chasing_blocked(self):
        f,s=sample()
        self.assertEqual(setup(f,s,D(2),D(108),D('108.1'))['reason'],'ENTRY_OUTSIDE_STRUCTURE_ZONE')
        f[-2]=(f[-2][0],D(105),D(102),D(103),D(100))
        self.assertEqual(setup(f,s,D(2),D('104.8'),D('104.9'))['reason'],'BROKEN_RETEST_STRUCTURE')

    def test_no_swings_no_structure(self):
        f,s=sample()
        self.assertEqual(trend(rows(range(100,170))),0)
        self.assertIn('STRUCTURE_1H',setup(f,rows(range(100,170)),D(2),D(105),D('105.1'))['reason'])

    def test_production_scanner_uses_structure_and_keeps_risk_checks(self):
        f,s=sample()
        data=fixture()
        now_ms=int(NOW.timestamp()*1000)
        def convert(r,seconds):
            # Flat warmup contributes no strict pivots.
            padded=rows([99]*(100-len(r)))+r
            end=now_ms//(seconds*1000)*seconds*1000
            return [[str(end-(100-i)*seconds*1000),str(c),str(h),str(lo),str(c),'1','1',str(v),'1']
                    for i,(_,h,lo,c,v) in enumerate(padded)]
        data['candles_15m']=convert(f,900)
        data['candles_1h']=convert(s,3600)
        data['ticker'].update(bidPx='104.8',askPx='104.81')
        cfg={**CONFIG,'setup_model':'structure_bos_retest_v1'}
        result=screen(data,cfg,NOW)
        self.assertEqual(result['status'],'WATCHLIST',result)
        self.assertEqual(result['candidate']['setup_model'],cfg['setup_model'])
        self.assertTrue(all(D(x['planned_risk'])<=1 for x in result['candidate']['sizing_options']))
        unclosed=data['candles_15m'][-1].copy()
        unclosed[8]='0'
        unclosed[4]='999999'
        data['candles_15m'].append(unclosed)
        self.assertEqual(screen(data,cfg,NOW),result)
        data['instrument']['minSz']='100000'
        self.assertEqual(screen(data,cfg,NOW)['status'],'WAIT')

    def test_expired_bos_is_not_reported_as_pending_retest(self):
        f,s=sample()
        for i in range(10):
            f.append(((len(f))*900000,D('105'),D('104.2'),D('104.8'),D(100)))
        result=setup(f,s,D(2),D('104.8'),D('104.9'))
        self.assertTrue(result['reason'].startswith('SETUP_EXPIRED'),result)
        self.assertGreater(result['evidence']['bos_age_bars'],8)
