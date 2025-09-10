# Shadow Protocol Vote Fetcher & Optimizer

Note: see in example subsection or in scripts/readme.md how to fetch data (incl. ts data for vol or not), optimize, and get lp output

This module provides two main tools for working with Shadow protocol voting data:

- **fetch_votes**: Fetches pool data and on-chain votes for a given period, saving a dashboard JSON file for analytics and optimization. 
- **optimizer**: Calculates the optimal allocation of your voting power to maximize bribe rewards, and compares your actual votes to the theoretical best.

Argument:

* `period` if specified, fetches votes for an elapsed period
* `historical_dashboard_path` to past bribes and fees -- is only useful to recompute the optimal votes for a past epoch ( bcs then the API doesn't broadcast the bribes and fees any more)
* we do not need any argument to see existing votes -- the `get_user_votes()`function in the shadow manager automatically fetches last (=existing) votes by the veNFT owner onchain


---

## 1. fetch_votes.py

#### Examples

Fetch votes dashboard for the next period:
```bash
python scripts/shadow/shadow_manager.py fetch
```

Fetch votes dashboard for a specific period:
```bash
python scripts/shadow/shadow_manager.py fetch --period 2899
```

Fetch historical votes for a previous period (using an existing dashboard):
```bash
python scripts/shadow/shadow_manager.py fetch --period 2898 --historical_dashboard_path data/shadow/historical/2898_votes_dashboard_170725.json
```

Fetch votes and include volatility data:
```bash
python scripts/shadow/shadow_manager.py fetch --with-volatility
```

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
| --with-volatility          | Include volatility data (slower but more comprehensive) | `--with-volatility`                                     |



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
python scripts/shadow/shadow_manager.py optimize
```

Optimize for a specific period:
```bash
python scripts/shadow/shadow_manager.py optimize --period 2899
```

Run historical optimization (removes your actual votes and re-optimizes):
```bash
python scripts/shadow/shadow_manager.py optimize --period 2898 --historical
```
You will be prompted for the path to the historical dashboard file.

Display results in the terminal:
```bash
python scripts/shadow/shadow_manager.py optimize --period 2899 --display
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


## 3. fetch_lp_data.py

### What It Does

- Fetches pools from the Shadow API
- Calculates emissions-based APR for both the current and next epoch
- For each pool, calculates:
  - Current epoch APR: Rewards being earned now based on votes from last epoch
  - Next epoch APR: Expected rewards for next epoch based on current votes
  - APR at different investment sizes (default: $1k, $10k, $50k)
  - How investment size impacts APR due to dilution effects
- Produces a comprehensive LP dashboard showing how APR scales with investment size
- Helps identify optimal pools for LP positions based on emissions rewards

### Example Output

```
================ LP DASHBOARD ================
Date: 2025-09-10
Showing top 30 pools by next epoch APR
----------------------------------------------
Pool                 TVL      Curr APR   Next APR  APR @ $1k  APR @ $10k APR @ $50k
----------------------------------------------
CL-USDC-stS-0.1047%   $231.26K    51.45%    50.89%     50.67%     48.92%     43.84%
wS/GOGLZ              $371.86K    70.59%    48.63%     48.53%     47.73%     44.72%
USDC/EUL                $0.09M     2.19%     2.05%      2.05%      2.04%      2.00%
...
```

### Usage

Run via the manager (shadow_manager.py):

```bash
python scripts/shadow/shadow_manager.py lp_dashboard
```

#### Flags

| Flag         | Description                                         | Example                            |
|--------------|-----------------------------------------------------|-----------------------------------|
| --sizes      | Custom investment sizes to calculate APR for        | `--sizes 5000 25000 100000`       |
| --top        | Number of top pools to display (default: 30)        | `--top 50`                        |
| --no-save    | Don't save the dashboard to file                    | `--no-save`                       |
| --no-display | Don't display the dashboard in terminal             | `--no-display`                    |

#### Examples

Generate default LP dashboard:
```bash
python scripts/shadow/shadow_manager.py lp_dashboard
```

Generate LP dashboard with custom investment sizes:
```bash
python scripts/shadow/shadow_manager.py lp_dashboard --sizes 5000 25000 100000
```

Show more pools in the dashboard:
```bash
python scripts/shadow/shadow_manager.py lp_dashboard --top 50
```

#### Output

- `lp_dashboard/shadow/lp_dashboard_{date}.json`
- `lp_dashboard/shadow/lp_dashboard.json`

---


# Aerodrome Finance Vote Fetcher & Optimizer

This module provides three main tools for working with Aerodrome protocol voting data:

- **fetch_votes**: Fetches pool data, on-chain votes, and relay votes, saving a dashboard JSON file for analytics and optimization.
- **optimizer**: Calculates the optimal allocation of your voting power to maximize fees and bribe rewards.
- **analytics**: Analyzes vote allocation performance and calculates expected returns.

## 1. fetch_votes.py

### What It Does

- Fetches all pools from the LpSugar contract.
- Filters for votable pools with active gauges.
- Enriches pools with token symbols.
- Fetches token prices from CoinGecko.
- Retrieves fees and bribes for the current epoch from RewardsSugar.
- Fetches relay votes if configured.
- Produces a dashboard file containing:
  - Pool information (address, symbol, type)
  - Current fees and bribes
  - On-chain vote weights
  - Our NFT votes
  - Relay votes (if applicable)

### Usage

This script is intended to be called from the manager (`aero_manager.py`):

```bash
python scripts/aero/aero_manager.py fetch
```

#### Flags

| Flag         | Description                            |
|--------------|----------------------------------------|
| --historical | Fetch historical data (not fully implemented yet) |

#### Output

- `input_data/aero/votes_dashboard.json`

## 2. optimizer.py

### What It Does

- Loads the votes dashboard file.
- Calculates the optimal allocation of your votes to maximize fees and bribes using an equal-marginal algorithm.
- Produces human-readable and bot-friendly output files.

### Example Output:

```json
{
  "total_expected_usd": 723.45,
  "allocations": [
    {
      "symbol": "WETH/USDC",
      "pool": "0x1234567890abcdef1234567890abcdef12345678",
      "votes": 1245.6789,
      "pct": 40,
      "exp_usd": 312.45
    },
    {
      "symbol": "AERO/USDC",
      "pool": "0xabcdef1234567890abcdef1234567890abcdef12",
      "votes": 987.6543,
      "pct": 35,
      "exp_usd": 254.32
    },
    {
      "symbol": "wstETH/WETH",
      "pool": "0x7890abcdef1234567890abcdef1234567890abcd",
      "votes": 765.4321,
      "pct": 25,
      "exp_usd": 156.68
    }
  ]
}
```

### Usage

Run via the manager (aero_manager.py):

```bash
python scripts/aero/aero_manager.py optimize
```

#### Flags

| Flag      | Description                                         |
|-----------|-----------------------------------------------------|
| --display | Display results in terminal instead of saving files |

#### Output

- optimized_votes_human.json: Human-readable JSON with vote allocations
- optimized_votes_bot.txt: Bot-friendly format for submitting votes

## 3. analytics.py

### What It Does

- Analyzes vote allocation performance.
- Calculates NFT value and expected returns.
- Computes forecasted APR.
- Optionally compares current votes with optimal allocation.

### Usage

Run via the manager (aero_manager.py):

```bash
python scripts/aero/aero_manager.py analyze
```

#### Flags

| Flag      | Description                                     |
|-----------|-------------------------------------------------|
| --compare | Compare with optimal allocation (if available)  |

#### Output

- analytics_report.json: Latest analytics report
- `analytics/aero/analytics_report_YYYYMMDD.json`: Date-stamped reports

### Example Output

When running analytics, you'll see a summary like:

```
================ ANALYTICS SUMMARY ================
Voting Power: 5000
Token Price: $0.45
NFT Value: $2250.00
Expected USD per Epoch: $723.45
Forecasted APR: 16.72%
==================================================
```

## Environment Setup

Ensure your .env file contains the following variables:

```
RPC_URL=<Base network RPC URL>
LP_SUGAR_ADDRESS=<LpSugar contract address>
REWARDS_SUGAR_ADDRESS=<RewardsSugar contract address>
VOTER_ADDRESS=<Voter contract address>
VE_ADDRESS=<Ve contract address>
NFT_ID=<Your veNFT ID>

# for relay support
RELAY_ACCOUNT=<Relay account address>
RELAY_SUGAR_ADDRESS=<RelaySugar contract address>

```

## Command Reference

Here's a quick reference for all available commands:

```bash
# Fetch votes data
python scripts/aero/aero_manager.py fetch

# Optimize votes
python scripts/aero/aero_manager.py optimize
python scripts/aero/aero_manager.py optimize --display

# Analyze votes
python scripts/aero/aero_manager.py analyze
python scripts/aero/aero_manager.py analyze --compare
```

## Notes

- The pools are fetched fresh from on-chain data for each run.
- Price data is fetched from CoinGecko during both fetch and analytics operations.
- The optimizer uses an equal-marginal algorithm to maximize expected returns.
- The dashboard is always saved to votes_dashboard.json as the primary data source.
```

