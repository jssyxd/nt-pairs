import traceback
from datetime import timedelta
from nautilus_trader.common import DataActor
from nautilus_trader.model import InstrumentId, BookType
from nt_pairs.live_node import load_live_plan, build_node

p = load_live_plan('configs/pair.json')
b = build_node(p, dry_run=True)
out = open('probe6.txt', 'w', encoding='utf-8')

class Probe(DataActor):
    def __new__(cls, *a, **k):
        return super().__new__(cls)
    def __init__(self):
        super().__init__()
        self.n = 0
    def on_start(self):
        for iid in p.instrument_ids:
            self.subscribe_book_deltas(InstrumentId.from_str(iid), BookType.L2_MBP, managed=True)
        self.clock.set_timer(name='p', interval=timedelta(seconds=20), callback=self.go)
    def on_book_deltas(self, deltas):
        self.n += 1
    def go(self, event=None):
        try:
            cache = self.cache
            for iid in p.instrument_ids:
                i = InstrumentId.from_str(iid)
                ob = cache.order_book(i)
                out.write('BOOK %s has=%s count=%s mid=%s\n' % (
                    iid, cache.has_order_book(i), cache.book_update_count(i),
                    None if ob is None else (str(ob.best_bid_price()), str(ob.best_ask_price()))))
            out.write('deltas %d\n' % self.n)
        except Exception:
            out.write(traceback.format_exc())
        out.flush()
        self.clock.cancel_timer('p')
        b.node.handle().stop()

b.node.add_actor(Probe())
b.node.run()
out.close()
