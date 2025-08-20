#!/usr/bin/env python3
import os
import json
import logging
import requests
from decimal import Decimal
from web3 import Web3
from dotenv import load_dotenv
import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

RPC_URL = os.getenv('SHADOW_RPC_URL')
VOTER_ADDRESS = os.getenv('SHADOW_VOTER_ADDRESS')
VOTER_ABI_PATH = os.getenv('VOTER_ABI_PATH', 'abi/shadow/Voter.json')
SHADOW_API_URL = os.getenv(
    "SHADOW_API_URL",
    "https://api.shadow.so/mixed-pairs?tokens=False&poolData=false"
)

def get_web3_and_contract():
    if not (RPC_URL and VOTER_ADDRESS):
        logger.error("❌ SHADOW_RPC_URL or SHADOW_VOTER_ADDRESS not set in .env")
        return None, None
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    if not w3.is_connected():
        logger.error("❌ Failed to connect to RPC node")
        return None, None
    try:
        with open(VOTER_ABI_PATH, 'r') as f:
            voter_abi = json.load(f)
        contract = w3.eth.contract(
            address=w3.to_checksum_address(VOTER_ADDRESS),
            abi=voter_abi
        )
        return w3, contract
    except Exception as e:
        logger.error(f"❌ Failed to load ABI or create contract: {e}")
        return None, None

def from_wei(val):
    return Decimal(val) / Decimal(10**18)

def get_current_period():
    _, contract = get_web3_and_contract()
    if not contract:
        return None
    return contract.functions.getPeriod().call() + 1  # match original script (+1)

def get_total_votes_period(period):
    _, contract = get_web3_and_contract()
    if not contract:
        return Decimal(0)
    try:
        raw = contract.functions.totalVotesPerPeriod(period).call()
        return from_wei(raw)
    except Exception as e:
        logger.error(f"❌ Failed to get total votes for period {period}: {e}")
        return Decimal(0)

def get_pool_votes_period(pool_addr, period):
    w3, contract = get_web3_and_contract()
    if not (w3 and contract):
        return Decimal(0)
    try:
        raw = contract.functions.poolTotalVotesPerPeriod(
            w3.to_checksum_address(pool_addr), period
        ).call()
        return from_wei(raw)
    except Exception as e:
        logger.error(f"❌ Failed to get votes for pool {pool_addr}, period {period}: {e}")
        return Decimal(0)

def fetch_pools_from_api():
    try:
        response = requests.get(SHADOW_API_URL)
        response.raise_for_status()
        data = response.json()
        pools = data.get("pairs", [])
        logger.info(f"🔍 Fetched {len(pools)} pools from Shadow API")
        # Filter active pools
        def is_active(pool):
            v2 = pool.get("gaugeV2") or {}
            if v2.get("isAlive", False):
                return True
            g = pool.get("gauge") or {}
            return bool(g.get("isAlive", False))
        active_pools = [p for p in pools if is_active(p)]
        logger.info(f"→ {len(active_pools)} active pools after filtering")
        # Sort by last 7d fees
        sorted_pools = sorted(
            active_pools,
            key=lambda p: p.get("stats", {}).get("last_7d_fees", 0),
            reverse=True
        )
        output = []
        for p in sorted_pools:
            stats = p.get("stats", {})
            entry = {
                "pool": p.get("id"),
                "symbol": p.get("symbol"),
                "fee_last_7d_usd": stats.get("last_7d_fees", 0),
                "vol_last_7d": stats.get("last_7d_vol", 0),
                "bribes_usd": p.get("voteBribesUsd", 0)
            }
            output.append(entry)
        return output
    except Exception as e:
        logger.error(f"❌ Failed to fetch pools from API: {e}")
        return []

def fetch_votes(period=None):
    """Fetch pools from API and votes for the given period, return dashboard dict"""
    if period is None:
        period = get_current_period()
        if period is None:
            logger.error("❌ Failed to get current period")
            return None

    pools = fetch_pools_from_api()
    if not pools:
        logger.error("❌ No pools fetched from API")
        return None

    seconds_per_week = 7 * 24 * 3600
    start_timestamp = period * seconds_per_week
    start_date = datetime.datetime.fromtimestamp(start_timestamp, tz=datetime.timezone.utc)
    date_str = start_date.strftime("%d%m%y")

    logger.info(f"ℹ️ Fetching votes for period {period}, starting on {start_date.strftime('%Y-%m-%d')}")

    total_votes = get_total_votes_period(period)
    logger.info(f"ℹ️ Total votes for period {period}: {total_votes}")

    augmented = []
    for entry in pools:
        pool_id = entry.get('pool')
        pool_votes = get_pool_votes_period(pool_id, period)
        e = entry.copy()
        e['pool_votes_period'] = float(pool_votes)
        augmented.append(e)

    augmented.sort(key=lambda x: x.get('pool_votes_period', 0), reverse=True)

    output = {
        'period': period,
        'start_date': start_date.isoformat(),
        'total_votes_period': float(total_votes),
        'pools': augmented
    }

    return output

def save_votes_dashboard(dashboard, period=None):
    """Save the votes dashboard to a file with period in the name"""
    if period is None and 'period' in dashboard:
        period = dashboard['period']
    if period is None:
        logger.error("❌ No period specified for saving dashboard")
        return False
    if 'start_date' in dashboard:
        date_obj = datetime.datetime.fromisoformat(dashboard['start_date'])
        date_str = date_obj.strftime("%d%m%y")
    else:
        date_str = datetime.datetime.now().strftime("%d%m%y")
    
    # Save period-specific file
    current_path = f'input_data/shadow/{period}_votes_dashboard.json'
    historical_path = f'input_data/shadow/historical/{period}_votes_dashboard_{date_str}.json'
    
    # Also save a generic dashboard file for compatibility
    generic_path = 'input_data/shadow/votes_dashboard.json'
    
    os.makedirs(os.path.dirname(current_path), exist_ok=True)
    with open(current_path, 'w') as f:
        json.dump(dashboard, f, indent=2)
    logger.info(f"✅ Saved current votes dashboard to {current_path}")
    
    # Save to generic path for easier access by optimizer
    with open(generic_path, 'w') as f:
        json.dump(dashboard, f, indent=2)
    logger.info(f"✅ Saved current votes dashboard to generic path {generic_path}")
    
    os.makedirs(os.path.dirname(historical_path), exist_ok=True)
    with open(historical_path, 'w') as f:
        json.dump(dashboard, f, indent=2)
    logger.info(f"✅ Saved votes dashboard for period {period} to {historical_path}")
    return True


def fetch_historical_votes(period, dashboard_path):
    """
    Load an existing dashboard for a historical period,
    fetch on-chain votes for each pool for that period,
    and save as <period>_historical_votes_dashboard.json.
    """
    if not os.path.exists(dashboard_path):
        logger.error(f"❌ Dashboard file {dashboard_path} not found.")
        return

    with open(dashboard_path, 'r') as f:
        dashboard = json.load(f)

    pools = dashboard.get('pools', [])
    for entry in pools:
        pool_id = entry.get('pool')
        if pool_id:
            entry['pool_votes_period'] = float(get_pool_votes_period(pool_id, period))

    dashboard['period'] = period
    dashboard['total_votes_period'] = float(get_total_votes_period(period))

    out_path = f"input_data/shadow/historical/{period}_historical_votes_dashboard.json"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump(dashboard, f, indent=2)
    logger.info(f"✅ Saved historical votes dashboard to {out_path}")

def run_fetch(period=None, historical_dashboard_path=None):
    """
    If historical_dashboard_path is provided, update that dashboard with on-chain votes for the given period.
    Otherwise, fetch current pools/bribes from API and on-chain votes.
    """
    if historical_dashboard_path:
        if period is None:
            logger.error("❌ Period must be specified for historical fetch")
            return
        logger.info(f"Fetching historical votes for period {period} using {historical_dashboard_path}")
        fetch_historical_votes(period, historical_dashboard_path)
    else:
        if period is None:
            period = get_current_period()
            logger.info(f"Fetching votes dashboard for current period {period}")
        else:
            logger.info(f"Fetching votes dashboard for period {period}")

        dashboard = fetch_votes(period)
        if dashboard:
            save_votes_dashboard(dashboard, period)
            logger.info(f"✅ Dashboard for period {period} saved/overwritten.")
        else:
            logger.error(f"❌ Failed to fetch dashboard for period {period}")