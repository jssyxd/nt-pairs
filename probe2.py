import time, traceback
from nautilus_trader.common import DataActor
from nautilus_trader.model import InstrumentId
from nt_pairs.live_node import load_live_plan, build_node

p = load_live_plan('configs/pair.json')
b = build_node(p, dry_run=True)
out = open('probe2.txt', 'w', encoding='utf-8')

class Probe(DataActor):
    def __new__(cls, *a, **k):
        return super().__new__(cls)
    def __init__(self):
        super().__init__()
    def on_start(self):
        self.clock.set_timer(name='p', interval=__import__('datetime').timedelta(seconds=15), callback=self.go)
    def go(self, event=None):
        try:
            cache = self.cache
            for iid in p.instrument_ids:
                inst = cache.instrument(InstrumentId.from_str(iid))
                out.write('INST %s -> %s\n' % (iid, inst))
                out.write('BOOK %s -> %s\n' % (iid, cache.order_book(InstrumentId.from_str(iid))))
            ids = cache.instrument_ids()
            out.write('COUNT %d\n' % len(ids))
            for i in list(ids)[:40]:
                out.write('  %s\n' % i)
        except Exception:
            out.write(traceback.format_exc())
        out.flush()
        self.clock.cancel_timer('p')
        b.node.handle().stop()

b.node.add_actor(Probe())
b.node.run()
out.close()
