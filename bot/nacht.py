"""Nacht-Betrieb: die ganze Nacht Daten sammeln und jede Stunde automatisch auswerten.
Sendet KEINE Orders (nur collector.py + Backtests auf den gespeicherten Daten).

Alles landet in EINEM Ordner nacht_<Tag_Uhrzeit>/:
    data_.../            Rohdaten pro Stunde (+ Backtest- und Analyse-Berichte darin)
    nacht_bericht.md     Zusammenfassung (wird jede Stunde neu geschrieben)
    nacht.log            was das Skript gerade tut
    nacht_collector.log  Ausgaben des Sammlers
    nacht_ergebnisse.jsonl  alle Zahlen maschinenlesbar

Start im Dev Environment (laeuft weiter, auch wenn das Terminal zu ist):
    cd /home/workspace
    nohup python bot/nacht.py > /dev/null 2>&1 &
Stand ansehen:   cat nacht_*/nacht_bericht.md      (oder: tail -f nacht_*/nacht.log)
Stoppen:         pkill -INT -f bot/nacht.py

ACHTUNG: Der Sammler haelt die EINE Team-Verbindung. Solange er laeuft, kann kein anderer
Bot von euch handeln (er wuerde den Sammler rauswerfen und umgekehrt).
Wird die Verbindung getrennt, wartet das Skript und verbindet neu. Nach mehreren
Trennungen kurz hintereinander wartet es laenger, um keinen Teamkollegen staendig rauszuwerfen.

Optionen:
    --chunk 60                      Minuten pro Datenordner
    --strategien simple,wall,wall_raw   Strategien fuer den Backtest (auch modul:Klasse)
"""
import argparse
import glob
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = "nacht_ergebnisse.jsonl"
REPORT = "nacht_bericht.md"
RETRY_WAIT = 60          # Sekunden nach einer Trennung
LONG_WAIT = 600          # nach 3 Trennungen innerhalb von 10 Minuten
MIN_MINUTES = 10         # kuerzere Ordner nicht auswerten


def log(msg):
    line = f"{datetime.now():%d.%m %H:%M:%S}  {msg}"
    print(line, flush=True)
    with open("nacht.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def data_dirs():
    return {d for d in glob.glob("data_*") if os.path.isdir(d) and not d.startswith("data_hist")}


def start_collector(cmd):
    before = data_dirs()
    out = open("nacht_collector.log", "a")
    proc = subprocess.Popen(shlex.split(cmd), stdout=out, stderr=subprocess.STDOUT)
    folder = None
    for _ in range(30):                       # warten, bis der Ordner da ist
        time.sleep(1)
        new = data_dirs() - before
        if new:
            folder = sorted(new)[-1]
            break
        if proc.poll() is not None:
            break
    return proc, folder


def stop_collector(proc):
    if proc.poll() is None:
        proc.send_signal(signal.SIGINT)       # Sammler speichert dann sauber
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()


def run(args_list, timeout=1800):
    try:
        r = subprocess.run([sys.executable] + args_list, capture_output=True, text=True,
                           timeout=timeout)
        return r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return "TIMEOUT"


def num(pattern, text, n=1):
    m = re.search(pattern, text)
    if not m:
        return [None] * n if n > 1 else None
    vals = [float(x) for x in m.groups()]
    return vals if n > 1 else vals[0]


def minutes_of(folder):
    path = os.path.join(folder, "books.csv")
    try:
        with open(path, encoding="utf-8") as f:
            f.readline()
            first = f.readline().split(",")[0]
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(size - 4000, 0))
            last = f.read().decode("utf-8", "ignore").strip().splitlines()[-1].split(",")[0]
        return (datetime.fromisoformat(last) - datetime.fromisoformat(first)).total_seconds() / 60
    except Exception:
        return 0.0


def analyse(folder, strategies):
    """Alle Auswertungen fuer einen fertigen Ordner. Gibt ein Ergebnis-Dict zurueck."""
    mins = minutes_of(folder)
    res = {"ordner": folder, "minuten": round(mins, 1), "zeit": datetime.now().isoformat(),
           "strategien": {}}
    if mins < MIN_MINUTES:
        res["hinweis"] = "zu kurz"
        return res
    try:
        with open(os.path.join(folder, "trades.csv"), encoding="utf-8") as f:
            res["trades"] = sum(1 for _ in f) - 1
    except OSError:
        res["trades"] = 0
    for s in strategies:
        out = run([os.path.join(HERE, "backtest.py"), folder, "--strategy", s])
        res["strategien"][s] = {
            "pnl": num(r"PnL gesamt \| ([+-]?[\d.]+)", out),
            "haelften": num(r"Haelfte \| ([+-]?[\d.]+) / ([+-]?[\d.]+)", out, 2),
            "fills": num(r"Fills \(passiv / aggressiv\) \| (\d+) / (\d+)", out, 2),
            "abbruch": "ABBRUCH" in out,
            "fehler": None if "PnL gesamt" in out else out[-300:],
        }
        log(f"  {folder} {s}: PnL {res['strategien'][s]['pnl']}")
    # weitere Analysen schreiben ihre Berichte in den Ordner
    for script in ("muster.py", "folge.py", "wand.py"):
        if os.path.exists(os.path.join(HERE, script)):
            run([os.path.join(HERE, script), folder])
    try:
        with open(os.path.join(folder, "muster.md"), encoding="utf-8") as f:
            m = re.search(r"HANDELBAR \((\d+)\)", f.read())
            res["muster_handelbar"] = int(m.group(1)) if m else None
    except OSError:
        pass
    return res


def write_report(strategies):
    rows = []
    if os.path.exists(RESULTS):
        with open(RESULTS, encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
    rows = [r for r in rows if r.get("strategien")]
    lines = [f"# Nacht-Bericht (Stand {datetime.now():%d.%m. %H:%M})", "",
             "Jede Zeile = ein Datenordner (ca. 1 Stunde). Backtest-PnL zur Mitte bewertet.",
             "Eine Strategie ist nur gut, wenn sie in den MEISTEN Stunden positiv ist.", ""]
    if not rows:
        lines.append("Noch keine fertige Stunde ausgewertet.")
    else:
        lines += ["## Gesamt", "", "| Strategie | Summe PnL | Stunden positiv | schlechteste Stunde | beste Stunde |",
                  "|---|---|---|---|---|"]
        for s in strategies:
            vals = [r["strategien"][s]["pnl"] for r in rows
                    if s in r["strategien"] and r["strategien"][s]["pnl"] is not None]
            if not vals:
                lines.append(f"| {s} | - | - | - | - |")
                continue
            pos = sum(v > 0 for v in vals)
            lines.append(f"| {s} | {sum(vals):+.1f} | {pos} von {len(vals)} | {min(vals):+.1f} | {max(vals):+.1f} |")
        lines += ["", "## Pro Stunde", "",
                  "| Ordner | Minuten | Trades | Muster handelbar | " + " | ".join(strategies) + " |",
                  "|---|---|---|---|" + "---|" * len(strategies)]
        for r in rows:
            cells = []
            for s in strategies:
                d = r["strategien"].get(s, {})
                if d.get("pnl") is None:
                    cells.append("Fehler")
                else:
                    cells.append(f"{d['pnl']:+.1f}" + (" (Abbruch)" if d.get("abbruch") else ""))
            lines.append(f"| {r['ordner']} | {r['minuten']} | {r.get('trades', '-')} | "
                         f"{r.get('muster_handelbar', '-')} | " + " | ".join(cells) + " |")
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def finish(folder, strategies):
    log(f"Werte {folder} aus ...")
    res = analyse(folder, strategies)
    with open(RESULTS, "a", encoding="utf-8") as f:
        f.write(json.dumps(res) + "\n")
    write_report(strategies)
    log(f"Bericht aktualisiert: {REPORT}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunk", type=float, default=60, help="Minuten pro Datenordner")
    ap.add_argument("--strategien", default="simple,wall,wall_raw")
    ap.add_argument("--collector", default=f"{sys.executable} -u {os.path.join(HERE, 'collector.py')}")
    ap.add_argument("--nur-auswerten", nargs="*", help="nur diese Ordner auswerten, nicht sammeln")
    ap.add_argument("--ordner", default="nacht_" + datetime.now().strftime("%d_%H%M"),
                    help="Ausgabeordner fuer alles")
    args = ap.parse_args()
    strategies = [s.strip() for s in args.strategien.split(",") if s.strip()]
    if args.nur_auswerten is not None:
        args.nur_auswerten = [os.path.abspath(f) for f in args.nur_auswerten]
    os.makedirs(args.ordner, exist_ok=True)
    os.chdir(args.ordner)                      # ab hier liegt alles in diesem Ordner

    if args.nur_auswerten is not None:
        for folder in args.nur_auswerten:
            finish(folder, strategies)
        return

    stop = {"now": False}
    signal.signal(signal.SIGINT, lambda *_: stop.update(now=True))
    signal.signal(signal.SIGTERM, lambda *_: stop.update(now=True))
    disconnects = []
    log(f"Nacht-Betrieb startet in {os.getcwd()}. Neuer Datenordner alle {args.chunk:.0f} min. "
        f"Strategien: {strategies}")
    proc, folder = start_collector(args.collector)
    log(f"Sammle in {folder}" if folder else "Sammler konnte nicht starten (nacht_collector.log)")
    while True:
        t0 = time.time()
        while not stop["now"] and proc.poll() is None and time.time() - t0 < args.chunk * 60:
            time.sleep(5)
        if stop["now"]:
            stop_collector(proc)
            if folder:
                finish(folder, strategies)
            break
        lost = proc.poll() is not None
        stop_collector(proc)
        old = folder
        if lost:
            now = time.time()
            disconnects = [t for t in disconnects if now - t < 600] + [now]
            wait = LONG_WAIT if len(disconnects) >= 3 else RETRY_WAIT
            log(f"Verbindung verloren (anderer Bot im Team?). Werte aus, dann {wait} s Pause.")
            if old:
                finish(old, strategies)
            for _ in range(wait):
                if stop["now"]:
                    break
                time.sleep(1)
            if stop["now"]:
                break
            proc, folder = start_collector(args.collector)
        else:
            # Stunde voll: sofort weitersammeln, dann die alte Stunde auswerten (keine Luecke)
            proc, folder = start_collector(args.collector)
            if old:
                finish(old, strategies)
        log(f"Sammle in {folder}" if folder else "Sammler konnte nicht starten (nacht_collector.log)")
    log("Nacht-Betrieb beendet.")


if __name__ == "__main__":
    main()
