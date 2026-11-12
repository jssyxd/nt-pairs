import traceback
from datetime import timedelta
from nautilus_trader.common import DataActor
from nt_pairs.live_node import load_live_plan, build_node

p = load_live_plan('configs/pair.json')
b = build_node(p, dry_run=True)
out = open('probe7.txt', 'w', encoding='utf-8')

class Probe(DataActor):
    def __new__(cls, *a, **k):
        return super().__new__(cls)
    def __init__(self):
        super().__init__()
    def on_start(self):
        self.clock.set_timer(name='p', interval=timedelta(seconds=25), callback=self.go)
    def go(self, event=None):
        try:
            s = b.strategy
            out.write('strategy book_ts=%s\n' % dict(s.book_ts))
            out.write('basis_history len=%d last=%s\n' % (len(s.basis_history), s.basis_history[-3:]))
            out.write('actor book_ts=%s\n' % dict(b.heartbeat._book_ts))
        except Exception:
            out.write(traceback.format_exc())
        out.flush()
        self.clock.cancel_timer('p')
        b.node.handle().stop()

b.node.add_actor(Probe())
b.node.run()
out.close()
