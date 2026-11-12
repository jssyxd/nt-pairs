import traceback
from nautilus_trader.common import DataActor
from nautilus_trader.model import InstrumentId, BookType
from nt_pairs.live_node import load_live_plan, build_node

p = load_live_plan('configs/pair.json')
b = build_node(p, dry_run=True)
out = open('probe3.txt', 'w', encoding='utf-8')

class Probe(DataActor):
    def __new__(cls, *a, **k):
        return super().__new__(cls)
    def __init__(self):
        super().__init__()
        self.n = 0
    def on_start(self):
        self.clock.set_timer(name='p', interval=__import__('datetime').timedelta(seconds=15), callback=self.go)
    def on_book_deltas(self, deltas):
        self.n += 1
        out.write('DELTA %d %s seq=%s snap=%s n=%d\n' % (self.n, deltas.instrument_id, deltas.sequence, deltas.is_snapshot, len(deltas.deltas)))
        out.flush()
    def go(self, event=None):
        try:
            cache = self.cache
            for iid in p.instrument_ids:
                i = InstrumentId.from_str(iid)
                out.write('HAS %s %s count=%s\n' % (iid, cache.has_order_book(i), cache.book_update_count(i)))
                out.write('TOP %s %s\n' % (iid, cache.top_of_book(i)))
            out.write('my deltas %d\n' % self.n)
        except Exception:
            out.write(traceback.format_exc())
        out.flush()
        self.clock.cancel_timer('p')
        b.node.handle().stop()

b.node.add_actor(Probe())
b.node.run()
out.close()
