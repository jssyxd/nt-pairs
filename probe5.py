import traceback
from datetime import timedelta
from nautilus_trader.common import DataActor
from nautilus_trader.model import InstrumentId, BookType
from nt_pairs.live_node import load_live_plan, build_node

p = load_live_plan('configs/pair.json')
b = build_node(p, dry_run=True)
out = open('probe5.txt', 'w', encoding='utf-8')

class Probe(DataActor):
    def __new__(cls, *a, **k):
        return super().__new__(cls)
    def __init__(self):
        super().__init__()
        self.n = 0
    def on_start(self):
        i = InstrumentId.from_str(p.leader.instrument_id)
        self.subscribe_book_deltas(i, BookType.L2_MBP)
        self.subscribe_quotes(i)
        self.clock.set_timer(name='p', interval=timedelta(seconds=20), callback=self.go)
    def on_book_deltas(self, deltas):
        self.n += 1
    def on_quote(self, quote):
        out.write('QUOTE %s %s\n' % (quote.instrument_id, quote.bid_price))
        out.flush()
    def go(self, event=None):
        try:
            cache = self.cache
            i = InstrumentId.from_str(p.leader.instrument_id)
            out.write('HAS %s count=%s\n' % (cache.has_order_book(i), cache.book_update_count(i)))
            ob = cache.order_book(i)
            out.write('BOOK %s\n' % (None if ob is None else (ob.best_bid_price(), ob.best_ask_price())))
            out.write('quotes %s\n' % cache.quotes(i))
            out.write('deltas %d\n' % self.n)
        except Exception:
            out.write(traceback.format_exc())
        out.flush()
        self.clock.cancel_timer('p')
        b.node.handle().stop()

b.node.add_actor(Probe())
b.node.run()
out.close()
