import io
import ssl
import urllib.request
import requests
import time
import yfinance as yf
import pandas as pd

class FetchStocks:
    def __init__(self, file):
        self.start = ""
        self.end = ""
        self.file = file
        self.tickers = []

    def get_all_tickers(self):
        from pathlib import Path
        local_cache = Path(__file__).resolve().parent / "tickers_sp500.csv"
        if local_cache.exists():
            try:
                df = pd.read_csv(local_cache)
                tickers = df['Symbol'].tolist()
                self.tickers = [t.replace('.', '-') for t in tickers]
                return df
            except Exception:
                pass

        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            df = pd.read_html(io.StringIO(resp.text))[0]
            df.to_csv(local_cache, index=False)
        except Exception as e:
            try:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
                    df = pd.read_html(io.StringIO(resp.read().decode('utf-8')))[0]
                    df.to_csv(local_cache, index=False)
            except Exception as e2:
                if local_cache.exists():
                    df = pd.read_csv(local_cache)
                else:
                    print(f"Error fetching Wikipedia S&P 500 table: {e2}")
                    raise

        tickers = df['Symbol'].tolist()
        tickers = [t.replace('.', '-') for t in tickers]
        self.tickers = tickers
        return df

    def get_russell3000_tickers(self, holdings_path=None, cache_file="tickers_russell3000.csv", fetch_sub_industries=False):
        """
        Parses iShares Russell 3000 ETF holdings export (e.g. IWV_holdings.csv),
        extracts all valid equity tickers, formats them into standard symbol notation
        compatible with Yahoo Finance, and updates self.tickers.

        Returns:
            pd.DataFrame: Formatted table of Russell 3000 components with Symbol, Security,
                          GICS Sector, GICS Sub-Industry, Sector, Weight, and Exchange.
        """
        from pathlib import Path
        root = Path(__file__).resolve().parent

        if holdings_path is None:
            # Check standard filenames
            for cand in ["IWV_holdings.csv", "IWV holding.csv", "IWV_holding.csv", "IWV holdings.csv"]:
                p = root / cand
                if p.exists():
                    holdings_path = p
                    break
            if holdings_path is None:
                holdings_path = root / "IWV_holdings.csv"
        else:
            holdings_path = Path(holdings_path)

        if not holdings_path.exists():
            raise FileNotFoundError(f"Holdings file not found: {holdings_path}")

        # Detect the header row dynamically
        skip = 0
        with open(holdings_path, 'r', encoding='utf-8', errors='ignore') as f:
            for idx, line in enumerate(f):
                cleaned_line = line.strip().replace('"', '')
                if cleaned_line.startswith(('Ticker,', 'Ticker;', 'Ticker\t')):
                    skip = idx
                    break

        df = pd.read_csv(holdings_path, skiprows=skip)
        df.columns = [c.strip() for c in df.columns]

        # Filter for Equity asset class if present
        if 'Asset Class' in df.columns:
            df = df[df['Asset Class'].astype(str).str.strip().str.lower() == 'equity']

        # Filter out missing or invalid tickers
        df = df[df['Ticker'].notna()]
        df = df[df['Ticker'].astype(str).str.strip() != '-']

        cleaned_rows = []
        for _, row in df.iterrows():
            raw_ticker = str(row['Ticker']).strip()
            # Standardize symbol notation: spaces & periods to hyphens (e.g. BRK B -> BRK-B, BF.B -> BF-B)
            sym = raw_ticker.replace(' ', '-').replace('.', '-')
            sec = str(row.get('Name', '')).strip()
            sector = str(row.get('Sector', 'Unassigned')).strip()

            cleaned_rows.append({
                'Symbol': sym,
                'Security': sec,
                'GICS Sector': sector,
                'GICS Sub-Industry': sector,
                'Sector': sector,
                'Asset Class': str(row.get('Asset Class', 'Equity')).strip(),
                'Weight (%)': row.get('Weight (%)', 0.0),
                'Exchange': str(row.get('Exchange', '')).strip()
            })

        out_df = pd.DataFrame(cleaned_rows)
        out_df = out_df.drop_duplicates(subset=['Symbol']).reset_index(drop=True)

        if cache_file:
            cache_path = root / cache_file
            out_df.to_csv(cache_path, index=False)

        self.tickers = out_df['Symbol'].tolist()

        if fetch_sub_industries:
            out_df = self.fetch_russell3000_sub_industries(tickers_df=out_df, cache_file=cache_file)

        return out_df

    def fetch_russell3000_sub_industries(self, tickers_df=None, cache_file="tickers_russell3000.csv", max_workers=25):
        """
        Enriches Russell 3000 ticker table with granular GICS Sub-Industry / Industry
        data using yfinance metadata queries in parallel.
        Caches the updated table with accurate 'GICS Sub-Industry' and 'GICS Sector' columns.

        Returns:
            pd.DataFrame: Table with granular sub-industries populated.
        """
        from concurrent.futures import ThreadPoolExecutor
        from pathlib import Path
        root = Path(__file__).resolve().parent

        if tickers_df is None:
            cache_path = root / cache_file if cache_file else None
            if cache_path and cache_path.exists():
                tickers_df = pd.read_csv(cache_path)
            else:
                tickers_df = self.get_russell3000_tickers(cache_file=cache_file, fetch_sub_industries=False)

        # Check if already populated with granular sub-industries to avoid rate limits
        if 'GICS Sub-Industry' in tickers_df.columns and tickers_df['GICS Sub-Industry'].nunique() > 20:
            print(f"Sub-industry metadata already populated ({tickers_df['GICS Sub-Industry'].nunique()} unique sub-industries). Skipping yfinance metadata queries to avoid rate limits.")
            return tickers_df

        symbols = tickers_df['Symbol'].tolist()
        print(f"Fetching granular sub-industry data for {len(symbols)} Russell 3000 tickers ({max_workers} worker threads)...")

        results = {}
        def _fetch_single_info(sym):
            try:
                t = yf.Ticker(sym)
                inf = t.info
                sec = inf.get('sector')
                ind = inf.get('industry')
                return sym, sec, ind
            except Exception:
                return sym, None, None

        n_done = 0
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for sym, sec, ind in executor.map(_fetch_single_info, symbols):
                results[sym] = (sec, ind)
                n_done += 1
                if n_done % 500 == 0 or n_done == len(symbols):
                    print(f"  Progress: {n_done}/{len(symbols)} tickers fetched...")

        # Enrich the DataFrame
        sub_industries = []
        sectors = []
        for _, row in tickers_df.iterrows():
            sym = row['Symbol']
            base_sec = str(row.get('Sector', 'Unassigned')).strip()
            yf_sec, yf_ind = results.get(sym, (None, None))

            sector_val = yf_sec if yf_sec else base_sec
            sub_ind_val = yf_ind if yf_ind else base_sec

            sectors.append(sector_val)
            sub_industries.append(sub_ind_val)

        tickers_df['GICS Sector'] = sectors
        tickers_df['GICS Sub-Industry'] = sub_industries

        if cache_file:
            save_path = root / cache_file
            tickers_df.to_csv(save_path, index=False)
            print(f"Successfully saved enriched sub-industry metadata to {save_path}")

        return tickers_df

    def get_tickers(self, ls):
        self.tickers = ls
        return self.tickers

    def fetch_data(self, start, end, batch_size=50, sleep_delay=1.0):
        self.start = start
        self.end = end
        if not self.tickers:
            self.get_all_tickers()

        print(f"Downloading historical data for {len(self.tickers)} tickers from {start} to {end}...")

        # Batch download with rate-limiting precautions (batch_size=50, delays, retries)
        if batch_size and len(self.tickers) > batch_size:
            dfs = []
            n = len(self.tickers)
            total_chunks = (n + batch_size - 1) // batch_size
            checkpoint_file = f"{self.file}.checkpoint.csv"

            for i in range(0, n, batch_size):
                chunk = self.tickers[i:i + batch_size]
                chunk_idx = i // batch_size + 1
                print(f"  Downloading chunk {chunk_idx}/{total_chunks} ({len(chunk)} tickers)...")

                c_df = None
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        c_data = yf.download(chunk, start=self.start, end=self.end, auto_adjust=False, progress=False)
                        if isinstance(c_data.columns, pd.MultiIndex):
                            if 'Close' in c_data.columns.get_level_values(0):
                                c_df = c_data['Close']
                            elif 'Adj Close' in c_data.columns.get_level_values(0):
                                c_df = c_data['Adj Close']
                            else:
                                c_df = c_data
                        else:
                            c_df = c_data['Close'] if 'Close' in c_data.columns else c_data

                        if c_df is not None and not c_df.empty:
                            break
                    except Exception as e:
                        print(f"    Attempt {attempt+1} failed: {e}")

                    if attempt < max_retries - 1:
                        backoff = 3.0 * (2 ** attempt)
                        print(f"    Rate-limit backoff: waiting {backoff:.1f}s before retry...")
                        time.sleep(backoff)

                if c_df is not None and not c_df.empty:
                    dfs.append(c_df)
                else:
                    print(f"  Warning: failed chunk {chunk_idx} after {max_retries} attempts.")

                # Save checkpoint every 5 chunks
                if chunk_idx % 5 == 0 and dfs:
                    try:
                        part_df = pd.concat(dfs, axis=1)
                        part_df = part_df.loc[:, ~part_df.columns.duplicated()]
                        part_df.to_csv(checkpoint_file)
                        print(f"    Checkpoint saved ({part_df.shape[1]} tickers so far)")
                    except Exception:
                        pass

                if sleep_delay > 0:
                    time.sleep(sleep_delay)

            df = pd.concat(dfs, axis=1) if dfs else pd.DataFrame()
            df = df.loc[:, ~df.columns.duplicated()]
        else:
            data = yf.download(self.tickers, start=self.start, end=self.end, auto_adjust=False)
            if isinstance(data.columns, pd.MultiIndex):
                if 'Close' in data.columns.get_level_values(0):
                    df = data['Close']
                elif 'Adj Close' in data.columns.get_level_values(0):
                    df = data['Adj Close']
                else:
                    df = data
            else:
                df = data['Close'] if 'Close' in data.columns else data

        df = df.dropna(axis=1, how='all')
        df.to_csv(self.file)
        print(f"Successfully saved {df.shape[1]} tickers and {df.shape[0]} trading days to {self.file}")
        return df

    def load_data_from_csv(self):
        df = pd.read_csv(self.file, index_col=0, parse_dates=True)
        return df


def get_russell3000_tickers(holdings_path=None, fetch_sub_industries=False):
    """Convenience function to get Russell 3000 tickers DataFrame."""
    loader = FetchStocks(file="stocks_russell3000.csv")
    return loader.get_russell3000_tickers(holdings_path=holdings_path, fetch_sub_industries=fetch_sub_industries)


def fetch_russell3000_sub_industries(cache_file="tickers_russell3000.csv", max_workers=25):
    """Convenience function to fetch granular sub-industries for Russell 3000 tickers."""
    loader = FetchStocks(file="stocks_russell3000.csv")
    return loader.fetch_russell3000_sub_industries(cache_file=cache_file, max_workers=max_workers)


if __name__ == "__main__":
    start = "2010-01-01"
    end = "2026-01-01"
    file = "stocks_russell3000.csv"

    loader = FetchStocks(file)
    loader.get_russell3000_tickers()
    loader.fetch_russell3000_sub_industries()
    loader.fetch_data(start, end, batch_size=50, sleep_delay=1.0)
