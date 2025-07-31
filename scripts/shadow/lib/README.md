# Shadow Protocol Vote Fetcher & Optimizer

This module provides two main tools for working with Shadow protocol voting data:

- **fetch_votes**: Fetches pool data and on-chain votes for a given period, saving a dashboard JSON file for analytics and optimization. 
- **optimizer**: Calculates the optimal allocation of your voting power to maximize bribe rewards, and compares your actual votes to the theoretical best.

Argument:

* `period` if specified, fetches votes for an elapsed period
* `historical_dashboard_path` to past bribes and fees -- is only useful to recompute the optimal votes for a past epoch ( bcs then the API doesn't broadcat the bribes and fees any more)
* we do not need any argument to see existing votes -- the `get_user_votes()`function in the shadow manager automatically fetches last (=existing) votes by the veNFT owner onchain


---

## 1. fetch_votes.py

### What It Does

- Fetches pools from the Shadow API.
- Fetches on-chain votes for each pool for the specified period.
- Produces a dashboard file containing:
  - `pool` (address)
  - `symbol`
  - `fee_last_7d_usd`
  - `vol_last_7d`
  - `bribes_usd`
  - `pool_votes_period`
- Saves the dashboard with the period number in the filename.

### Usage

This script is intended to be called from the manager (`shadow_manager.py`), but you can also run its functions directly.

#### Main Function

- `run_fetch(period=None, historical_dashboard_path=None)`

  - `period` (optional): Integer period number to fetch. If not provided, fetches for the next period.
  - `historical_dashboard_path` (optional): Path to an existing dashboard file for historical fetches.

#### Flags (when used via manager)

| Flag                       | Description                                         | Example                                                    |
|----------------------------|-----------------------------------------------------|------------------------------------------------------------|
| --period                   | Specify the period number to fetch                  | `--period 2899`                                            |
| --historical_dashboard_path| Path to dashboard for historical fetch              | `--historical_dashboard_path data/shadow/2898_votes_dashboard.json` |



#### Examples

Fetch votes dashboard for the next period:
```bash
python shadow_manager.py fetch
```

Fetch votes dashboard for a specific period:
```bash
python shadow_manager.py fetch --period 2899
```

Fetch historical votes for a previous period (using an existing dashboard):
```bash
python shadow_manager.py fetch --period 2898 --historical_dashboard_path data/shadow/historical/2898_votes_dashboard_170725.json
```

#### Output

- `data/shadow/{period}_votes_dashboard.json`
- `data/shadow/historical/{period}_votes_dashboard_{date}.json`
- For historical fetches: `data/shadow/historical/{period}_historical_votes_dashboard.json`

---

## 2. optimizer.py

### What It Does

- Loads a dashboard file for a given period.
- Fetches your voting power from the blockchain.
- Calculates the optimal allocation of your votes to maximize bribe rewards.
- For historical optimization, removes your actual votes from the dashboard and re-optimizes.
- Saves or displays the results in human-readable and bot formats.

### Example Output:
```json

{
  "total_expected_usd": 561.21,
  "allocations": [
    {
      "symbol": "CL-wS-GOGLZ-0.5%",
      "pool": "0x1f4efc47e5a5ab6539d95a76e2dde6d74462acea",
      "votes": 2695.6835792317534,
      "pct": 45,
      "exp_usd": 256.72
    },
    {
      "symbol": "CL-wS-NAVI-2.0%",
      "pool": "0x28f1bb2952ae8742b9e16fd515e3d01f4be6bc30",
      "votes": 1766.5794435975142,
      "pct": 29,
      "exp_usd": 167.14
    },
    {
      "symbol": "CL-USDC-stS-0.1093%",
      "pool": "0x2bcb79fd1e0c4251b6f94daee25d4c6ff330cdf8",
      "votes": 1537.1868359668588,
      "pct": 26,
      "exp_usd": 137.35
    }
  ],
  "re_run": false,
  "period": 2900
}
```


### Usage

Run via the manager (`shadow_manager.py`):

#### Main Function

- `run_optimize(period=None, save=True, is_historical=False)`

  - `period` (optional): Period to optimize for. If not provided, uses the next period.
  - `save` (optional): If `True`, saves results to file. If `False`, displays in terminal.
  - `is_historical` (optional): If `True`, runs historical optimization.

#### Flags (when used via manager)

| Flag         | Description                                         | Example                                                    |
|--------------|-----------------------------------------------------|------------------------------------------------------------|
| --period     | Specify the period to optimize                      | `--period 2899`                                            |
| --historical | Run historical optimization                         | `--historical`                                             |
| --display    | Display results in terminal instead of saving       | `--display`                                                |

#### Examples

Optimize for the next period (default):
```bash
python shadow_manager.py optimize
```

Optimize for a specific period:
```bash
python shadow_manager.py optimize --period 2899
```

Run historical optimization (removes your actual votes and re-optimizes):
```bash
python shadow_manager.py optimize --period 2898 --historical
```
You will be prompted for the path to the historical dashboard file.

Display results in the terminal:
```bash
python shadow_manager.py optimize --period 2899 --display
```

#### Output

- Current optimization:
  - `optimized_votes/shadow/{period}_optimized_votes_human.json`
  - `optimized_votes/shadow/{period}_optimized_votes_bot.txt`
  - Also saved to: `optimized_votes/shadow/optimized_votes_human.json` and `optimized_votes/shadow/optimized_votes_bot.txt`
- Historical optimization:
  - `optimized_votes/shadow/historical/{period}_historical_optimal_votes.json`
  - `optimized_votes/shadow/historical/{period}_historical_optimal_votes_bot.txt`

---

## Analytics & Comparison



## Notes

- Make sure your `.env` file is set up with the correct RPC and contract addresses.
- The pools are always fetched fresh from the API for each run (except for historical fetches).
- For historical optimization, you must provide the dashboard file for the period you want to analyze.

---
