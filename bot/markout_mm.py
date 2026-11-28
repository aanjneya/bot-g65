"""Markout-gesteuerter Market Maker (Testversion fuer den Backtester).

Idee in einfachen Worten:
  1. Wir messen fuer jede Aktie und jede Seite (Kauf / Verkauf), was der Preis 1 Sekunde
     nach unserem Fill gemacht hat (Markout). Ist der Durchschnitt klar negativ, werden wir
     dort von Informierten abgeholt.
  2. Dann gehen wir mit dieser Seite weg vom besten Preis (breiter) oder ziehen sie
     ganz zurueck. Wird der Wert wieder besser, kommen wir zurueck.
  3. Sprung-Schutz: bewegt sich der Mittelpreis schnell, ziehen wir kurz beide Seiten
     zurueck, statt mit veralteten Preisen im Buch zu stehen.

Nutzung im Backtester:
  python bot/backtest.py <datenordner> --strategy markout_mm:Voll
  Varianten fuer den Vergleich: Voll, NurMarkout, NurSchutz, Basis (= wie 'simple')

Wenige Parameter mit Absicht (gegen Ueberanpassung):
  SCHWELLE (ab welchem Markout wir breiter gehen), AUS_SCHWELLE (ab wann Seite aus),
  PAUSE (wie lange Seite aus), SPRUNG (Ticks), SPRUNG_ZEIT (Sekunden).
"""
import math

TICK = 0.1


class _Basis:
    LOT, MAX_POS = 5, 30
    MARKOUT_AN = False
    SCHUTZ_AN = False
    # Markout-Regel
    HORIZONT = 1.0        # Sekunden bis wir den Markout eines Fills messen
    HALBWERT = 8          # EWMA ueber ca. so viele Fills
    MIN_FILLS = 6         # vorher nichts aendern
    SCHWELLE = -0.03      # EWMA unter diesem Wert -> 1 Tick weiter weg
    AUS_SCHWELLE = -0.08  # darunter -> Seite aus
    PAUSE = 20.0          # Sekunden Seite aus
    # Sprung-Schutz
    SPRUNG = 2            # Ticks
    SPRUNG_ZEIT = 2.0     # Sekunden
    SCHUTZ_PAUSE = 2.0
    # Anfragen-Bremse (Optibook trennt ab 25 pro Sekunde)
    RATE = 18.0           # erlaubte Anfragen pro Sekunde (Dauer)
    BURST = 10.0          # kurzzeitig erlaubter Vorrat

    def __init__(self, ex, folder):
        self.ex = ex
        self.inst = list(ex.instruments)
        self.seen = 0
        self.pending = []                                   # Fills, deren Markout noch fehlt
        self.ewma = {(i, s): 0.0 for i in self.inst for s in ("bid", "ask")}
        self.n = {(i, s): 0 for i in self.inst for s in ("bid", "ask")}
        self.aus_bis = {(i, s): 0.0 for i in self.inst for s in ("bid", "ask")}
        self.hist = {i: [] for i in self.inst}              # (zeit, mid)
        self.schutz_bis = {i: 0.0 for i in self.inst}
        self.log = []                                       # Entscheidungen fuer die Auswertung
        self.alpha = 2.0 / (self.HALBWERT + 1)
        self.tokens = self.BURST
        self.t_last = None
        self.start = 0
        self.uebersprungen = 0                              # Updates, die die Bremse verhindert hat

    # ---- Markout ----
    def _update_markouts(self, now):
        fills = self.ex.fills
        while self.seen < len(fills):
            self.pending.append(fills[self.seen])
            self.seen += 1
        rest = []
        for f in self.pending:
            if now - f.timestamp < self.HORIZONT:
                rest.append(f)
                continue
            m = self.ex.mid(f.instrument_id)
            if m is None:
                rest.append(f)
                continue
            s = 1 if f.side == "bid" else -1
            mo = s * (m - f.price)
            k = (f.instrument_id, f.side)
            self.n[k] += 1
            a = self.alpha if self.n[k] > 1 else 1.0
            self.ewma[k] = (1 - a) * self.ewma[k] + a * mo
            if self.MARKOUT_AN and self.n[k] >= self.MIN_FILLS and self.ewma[k] < self.AUS_SCHWELLE:
                self.aus_bis[k] = now + self.PAUSE
                self.log.append((now, k, "aus", round(self.ewma[k], 3)))
        self.pending = rest

    def _weiter(self, k):
        """Wie viele Ticks weiter weg vom besten Preis soll diese Seite quoten."""
        if not self.MARKOUT_AN:
            return 0
        if self.n[k] >= self.MIN_FILLS and self.ewma[k] < self.SCHWELLE:
            return 1
        return 0

    # ---- Sprung-Schutz ----
    def _schutz(self, i, now, mid):
        h = self.hist[i]
        h.append((now, mid))
        while h and now - h[0][0] > self.SPRUNG_ZEIT:
            h.pop(0)
        if self.SCHUTZ_AN and h:
            lo = min(m for _, m in h)
            hi = max(m for _, m in h)
            if (hi - lo) >= self.SPRUNG * TICK - 1e-9:
                self.schutz_bis[i] = now + self.SCHUTZ_PAUSE
        return now < self.schutz_bis[i]

    def step(self):
        ex = self.ex
        now = ex.clock.time()
        self._update_markouts(now)
        if self.t_last is not None:
            self.tokens = min(self.BURST, self.tokens + (now - self.t_last) * self.RATE)
        self.t_last = now
        pos = ex.get_positions()
        n = len(self.inst)
        self.start = (self.start + 1) % n                  # reihum, damit keine Aktie verhungert
        for i in self.inst[self.start:] + self.inst[:self.start]:
            b = ex.book[i]
            if not b[0] or not b[1]:
                continue
            bb, ba = b[0][0][0], b[1][0][0]
            mid = (bb + ba) / 2
            pausiert = self._schutz(i, now, mid)
            mine = ex.get_outstanding_orders(i)
            p = pos[i]
            skew = -round(p / self.MAX_POS * 2) * TICK
            want = {"bid": None, "ask": None}
            if not pausiert:
                kb, ka = (i, "bid"), (i, "ask")
                if p < self.MAX_POS and now >= self.aus_bis[kb]:
                    want["bid"] = (round(bb + skew - self._weiter(kb) * TICK, 1), self.LOT)
                if p > -self.MAX_POS and now >= self.aus_bis[ka]:
                    want["ask"] = (round(ba + skew + self._weiter(ka) * TICK, 1), self.LOT)
            if want["bid"] and want["bid"][0] >= ba:
                want["bid"] = None
            if want["ask"] and want["ask"][0] <= bb:
                want["ask"] = None
            if want["bid"] and want["ask"] and want["bid"][0] >= want["ask"][0]:
                want["bid"] = None
            have = {(o.side, o.price, o.volume) for o in mine.values()}
            target = {(s, *w) for s, w in want.items() if w}
            if have != target:
                kosten = (1 if mine else 0) + len(target)
                if kosten > self.tokens:
                    self.uebersprungen += 1
                    continue
                self.tokens -= kosten
                if mine:
                    ex.delete_orders(i)
                for s, pr, v in target:
                    ex.insert_order(i, price=pr, volume=v, side=s, order_type="limit")


class Basis(_Basis):
    """Gleich wie 'simple' (Vergleichswert)."""


class NurMarkout(_Basis):
    MARKOUT_AN = True


class NurSchutz(_Basis):
    SCHUTZ_AN = True


class Voll(_Basis):
    MARKOUT_AN = True
    SCHUTZ_AN = True
