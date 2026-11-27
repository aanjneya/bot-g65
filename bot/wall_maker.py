"""Wand-Bot (Penny Jumping): quotet 1 Tick VOR grossen Orders (Waenden).
Steht eine grosse Kauf-Order bei 100.0, kaufen wir bei 100.1. Faellt der Preis,
verkaufen wir an die Wand (Verlust nur ~1 Tick). Spiegelbildlich auf der Verkaufsseite.

Start (Probe, sendet KEINE Orders, zeigt nur was er tun wuerde):
    python bot/wall_maker.py
Live:
    python bot/wall_maker.py --live
Ctrl+C beendet: alle Orders weg, Positionen werden per IOC glattgestellt.

ACHTUNG: Nur EINE Verbindung pro Team. Jeder Start wirft einen anderen Bot raus.
Schreibt wall_log_<Uhrzeit>/fills.csv und markout.csv (Preis 1/5/15 s nach jedem Fill).
"""
import argparse
import csv
import logging
import os
import time
from collections import deque
from datetime import datetime

INSTRUMENTS = ["AMD", "AVGO", "CRWV", "MU", "NBIS", "NVDA", "SNDK", "VRT"]
TICK = 0.1
WALL = 400          # ab dieser Menge (anderer Teilnehmer) gilt eine Stufe als Wand
MAX_DIST = 3        # Wand hoechstens so viele Ticks vom besten Preis
LOT = 10            # Stueck pro Order
MAX_POS = 40        # eigenes Limit pro Aktie (Boerse erlaubt 100)
HOLD_MAX = 15       # Sekunden: spaetestens dann glattstellen
LOSS_STOP = 500.0   # Gesamtverlust, ab dem der Bot alles schliesst und stoppt
MAX_REQ = 15        # Anfragen pro Sekunde (Boerse trennt ab 25)
LOOP = 0.2          # Sekunden pro Durchlauf
MARKOUTS = [1, 5, 15]
EPS = 1e-9
MICRO_FILTER = True  # nur einsteigen, wenn der Microprice nicht gegen uns zeigt
CAP = 100           # Mengen fuer den Microprice kappen (sonst dominiert die Wand selbst)


def r1(x):
    return round(x + 0.0, 1)


# ---------- reine Logik (ohne Boerse, testbar) ----------

def others(levels, own):
    """Buchstufen ohne unsere eigene Menge. levels=[(p,v)], own={p: v}."""
    out = []
    for p, v in levels:
        v2 = v - own.get(r1(p), 0)
        if v2 > 0:
            out.append((r1(p), v2))
    return out


def find_wall(levels, side):
    if not levels:
        return None
    best = levels[0][0]
    for p, v in levels:
        dist = (best - p) if side == "bid" else (p - best)
        if dist > MAX_DIST * TICK + EPS:
            break
        if v >= WALL:
            return p
    return None


def volume_at(levels, price):
    for p, v in levels:
        if abs(p - price) < EPS:
            return v
    return 0


def must_flatten(pos, st, bids, asks, now):
    """Schutz weg oder zu lange gehalten? bids/asks = Buch ohne uns."""
    if pos == 0 or not bids or not asks:
        return None
    if now - st["since"] > HOLD_MAX:
        return "Zeit"
    w = st.get("wall")
    if pos > 0:
        if w is None:
            return "keine Wand"
        if bids[0][0] < w - EPS:
            return "Wand gebrochen"
        if volume_at(bids, w) < WALL / 2 and bids[0][0] <= st["entry"] + EPS:
            return "Wand weg"
    else:
        if w is None:
            return "keine Wand"
        if asks[0][0] > w + EPS:
            return "Wand gebrochen"
        if volume_at(asks, w) < WALL / 2 and asks[0][0] >= st["entry"] - EPS:
            return "Wand weg"
    return None


def micro_tilt(bids, asks):
    """Microprice minus Mitte. >0: Druck nach oben, <0: Druck nach unten."""
    qb, qa = min(bids[0][1], CAP), min(asks[0][1], CAP)
    micro = (bids[0][0] * qa + asks[0][0] * qb) / (qb + qa)
    return micro - (bids[0][0] + asks[0][0]) / 2


def plan_quotes(pos, st, bids, asks):
    """Gewuenschte Orders {'bid': (preis, menge) | None, 'ask': ...}."""
    want = {"bid": None, "ask": None}
    if not bids or not asks:
        return want
    best_bid, best_ask = bids[0][0], asks[0][0]
    bw, aw = find_wall(bids, "bid"), find_wall(asks, "ask")
    if MICRO_FILTER:
        tilt = micro_tilt(bids, asks)
        if tilt < -EPS:
            bw = None       # Druck nach unten: nicht vor der Kauf-Wand kaufen
        if tilt > EPS:
            aw = None       # Druck nach oben: nicht vor der Verkaufs-Wand verkaufen

    # Einstieg vor der Wand
    if bw is not None and pos < MAX_POS:
        p = r1(bw + TICK)
        if p < best_ask - EPS:
            want["bid"] = (p, min(LOT, MAX_POS - pos))
    if aw is not None and pos > -MAX_POS:
        p = r1(aw - TICK)
        if p > best_bid + EPS:
            want["ask"] = (p, min(LOT, MAX_POS + pos))

    # Ausstieg: Position passiv mit Gewinn loswerden
    if pos > 0:
        p = max(r1(st["entry"] + TICK), r1(best_bid + TICK))
        if want["ask"] is not None:
            p = min(p, want["ask"][0]) if want["ask"][0] > st["entry"] + EPS else p
        want["ask"] = (r1(p), pos)
        want["bid"] = None if pos >= LOT else want["bid"]   # nicht weiter aufbauen
    elif pos < 0:
        p = min(r1(st["entry"] - TICK), r1(best_ask - TICK))
        if want["bid"] is not None:
            p = max(p, want["bid"][0]) if want["bid"][0] < st["entry"] - EPS else p
        want["bid"] = (r1(p), -pos)
        want["ask"] = None if -pos >= LOT else want["ask"]

    # Niemals gegen uns selbst handeln (Optibook trennt sonst die Verbindung)
    if want["bid"] and want["ask"] and want["bid"][0] >= want["ask"][0] - EPS:
        if pos > 0:
            want["bid"] = None
        elif pos < 0:
            want["ask"] = None
        else:
            want["bid"] = want["ask"] = None
    return want


# ---------- Boerse ----------

class RateLimit:
    def __init__(self, per_sec):
        self.per_sec, self.times = per_sec, deque()

    def ok(self):
        now = time.monotonic()
        while self.times and now - self.times[0] >= 1.0:
            self.times.popleft()
        if len(self.times) < self.per_sec:
            self.times.append(now)
            return True
        return False

    def wait(self):
        while not self.ok():
            time.sleep(0.05)


class DryExchange:
    """Liest echte Marktdaten, sendet aber nichts."""

    def __init__(self, real):
        self.real = real
        self.shown = {}

    def __getattr__(self, name):
        return getattr(self.real, name)

    def insert_order(self, inst, *, price, volume, side, order_type="limit"):
        key = (inst, side, order_type)
        if self.shown.get(key) != (price, volume):   # nur Aenderungen zeigen
            self.shown[key] = (price, volume)
            print(f"  [PROBE] {datetime.now():%H:%M:%S} wuerde {order_type} {side} "
                  f"{volume} {inst} @ {price:.1f}")

        class R:
            success, order_id, error_reason = True, None, None
        return R()

    def delete_order(self, inst, *, order_id):
        pass

    def delete_orders(self, inst):
        pass

    def get_outstanding_orders(self, inst):
        return {}

    def get_positions(self):
        return {i: 0 for i in INSTRUMENTS}

    def poll_new_trades(self, inst):
        return []


class WallMaker:
    def __init__(self, ex, folder):
        self.ex = ex
        self.rl = RateLimit(MAX_REQ)
        self.state = {i: {"since": 0.0, "entry": 0.0, "wall": None} for i in INSTRUMENTS}
        self.last_pos = {i: 0 for i in INSTRUMENTS}
        self.pending = []   # offene Markout-Messungen
        self.folder = folder
        self.fills = csv.writer(open(os.path.join(folder, "fills.csv"), "w", newline=""))
        self.fills.writerow(["time", "instrument", "side", "price", "volume", "pos_after"])
        self.mk = csv.writer(open(os.path.join(folder, "markout.csv"), "w", newline=""))
        self.mk.writerow(["time", "instrument", "side", "price", "volume"]
                         + [f"markout_{h}s" for h in MARKOUTS])
        self.mk_sum = {}

    # --- Hilfen ---
    def book(self, inst, own_bid, own_ask):
        b = self.ex.get_last_price_book(inst)
        if b is None:
            return [], []
        bids = others([(l.price, l.volume) for l in b.bids], own_bid)
        asks = others([(l.price, l.volume) for l in b.asks], own_ask)
        return bids, asks

    def flatten(self, inst, pos, why):
        print(f"{datetime.now():%H:%M:%S} {inst}: glattstellen {pos} ({why})")
        self.rl.wait()
        self.ex.delete_orders(inst)          # erst eigene Orders weg (Self-Trade!)
        b = self.ex.get_last_price_book(inst)
        if b is None:
            return
        if pos > 0 and b.bids:
            side, price = "ask", b.bids[0].price
        elif pos < 0 and b.asks:
            side, price = "bid", b.asks[0].price
        else:
            return
        self.rl.wait()
        try:
            self.ex.insert_order(inst, price=r1(price), volume=abs(pos), side=side, order_type="ioc")
        except Exception as exc:
            print("IOC fehlgeschlagen:", exc)

    def track_fills(self, inst, mid, pos, now):
        last_price = None
        for t in self.ex.poll_new_trades(inst):
            self.fills.writerow([datetime.now().isoformat(), inst, t.side, t.price, t.volume, pos])
            self.pending.append({"t": now, "inst": inst, "side": t.side, "price": t.price,
                                 "vol": t.volume, "marks": {}})
            last_price = t.price
        prev = self.last_pos[inst]
        st = self.state[inst]
        opened = pos != 0 and (prev == 0 or (prev > 0) != (pos > 0))
        if opened:   # neue Position (oder Seitenwechsel): Einstieg merken
            st["since"] = now
            st["entry"] = last_price if last_price is not None else mid
            st["wall"] = None
        if pos == 0:
            st["wall"] = None
        self.last_pos[inst] = pos

    def update_markouts(self, mids, now):
        keep = []
        for p in self.pending:
            mid = mids.get(p["inst"])
            for h in MARKOUTS:
                if h not in p["marks"] and now - p["t"] >= h and mid:
                    sign = 1 if p["side"] == "bid" else -1
                    p["marks"][h] = sign * (mid - p["price"])
            if len(p["marks"]) == len(MARKOUTS):
                self.mk.writerow([datetime.now().isoformat(), p["inst"], p["side"], p["price"], p["vol"]]
                                 + [f"{p['marks'][h]:+.3f}" for h in MARKOUTS])
                s = self.mk_sum.setdefault(p["inst"], [0.0, 0])
                s[0] += p["marks"][5]
                s[1] += 1
            else:
                keep.append(p)
        self.pending = keep

    def sync(self, inst, orders, want):
        """Erst ALLE veralteten Orders loeschen, dann neue setzen.
        So kann eine neue Order nie gegen eine alte eigene laufen."""
        to_insert = []
        for side in ("bid", "ask"):
            mine = [o for o in orders.values() if o.side == side]
            target = want[side]
            if target and len(mine) == 1 and abs(mine[0].price - target[0]) < EPS \
                    and mine[0].volume == target[1]:
                continue
            for o in mine:
                if not self.rl.ok():
                    return          # naechster Durchlauf macht weiter
                self.ex.delete_order(inst, order_id=o.order_id)
            if target:
                to_insert.append((side, target))
        for side, (price, vol) in to_insert:
            if not self.rl.ok():
                return
            try:
                r = self.ex.insert_order(inst, price=price, volume=vol, side=side,
                                         order_type="limit")
                if not r.success:
                    print(f"{inst} {side} abgelehnt: {r.error_reason}")
            except Exception as exc:
                print(f"{inst} {side} Fehler: {exc}")

    # --- ein Durchlauf ---
    def step(self):
        now = time.time()
        positions = self.ex.get_positions()
        mids = {}
        for inst in INSTRUMENTS:
            orders = self.ex.get_outstanding_orders(inst)
            own_bid, own_ask = {}, {}
            for o in orders.values():
                d = own_bid if o.side == "bid" else own_ask
                d[r1(o.price)] = d.get(r1(o.price), 0) + o.volume
            bids, asks = self.book(inst, own_bid, own_ask)
            if not bids or not asks:
                continue
            mid = (bids[0][0] + asks[0][0]) / 2
            mids[inst] = mid
            pos = int(positions.get(inst, 0))
            self.track_fills(inst, mid, pos, now)
            st = self.state[inst]
            if pos != 0 and st["wall"] is None:
                st["wall"] = find_wall(bids, "bid") if pos > 0 else find_wall(asks, "ask")

            why = must_flatten(pos, st, bids, asks, now)
            if why:
                self.flatten(inst, pos, why)
                continue

            want = plan_quotes(pos, st, bids, asks)
            self.sync(inst, orders, want)
        self.update_markouts(mids, now)

    def stop(self, flatten=True):
        for inst in INSTRUMENTS:
            self.rl.wait()
            self.ex.delete_orders(inst)
        if flatten:
            for inst, pos in self.ex.get_positions().items():
                if pos:
                    self.flatten(inst, pos, "Ende")

    def status(self):
        pnl = self.ex.get_pnl()
        pos = {k: v for k, v in self.ex.get_positions().items() if v}
        mk = {k: f"{s[0] / s[1]:+.3f}" for k, s in self.mk_sum.items() if s[1]}
        print(f"{datetime.now():%H:%M:%S} PnL={pnl} Pos={pos} Markout5s={mk}")
        return pnl


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="echte Orders senden")
    ap.add_argument("--no-flatten", action="store_true", help="beim Beenden nicht glattstellen")
    args = ap.parse_args()

    from optibook.synchronous_client import Exchange
    logging.getLogger("client").setLevel("ERROR")

    folder = "wall_log_" + datetime.now().strftime("%d_%H%M%S")
    os.makedirs(folder)
    real = Exchange()
    real.connect()
    ex = real if args.live else DryExchange(real)
    print("LIVE" if args.live else "PROBE (keine Orders)", "- Log in", folder)
    start_pnl = real.get_pnl() or 0.0
    bot = WallMaker(ex, folder)
    last = 0.0
    try:
        while real.is_connected():
            bot.step()
            if time.time() - last > 10:
                pnl = bot.status() or 0.0
                last = time.time()
                if args.live and pnl - start_pnl < -LOSS_STOP:
                    print("VERLUSTSTOPP erreicht.")
                    break
            time.sleep(LOOP)
        else:
            print("Verbindung verloren (hat sich jemand aus dem Team verbunden?)")
    except KeyboardInterrupt:
        print("Gestoppt.")
    finally:
        if real.is_connected():
            bot.stop(flatten=not args.no_flatten)
            time.sleep(1)
            bot.status()
            real.disconnect()


if __name__ == "__main__":
    main()
