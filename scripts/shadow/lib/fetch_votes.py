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

import time

RPC_URL = os.getenv('SHADOW_RPC_URL')
VOTER_ADDRESS = os.getenv('SHADOW_VOTER_ADDRESS')
VOTER_ABI_PATH = os.getenv('VOTER_ABI_PATH', 'abi/shadow/Voter.json')
SHADOW_API_URL = os.getenv(
    "SHADOW_API_URL",
    "https://api.shadow.so/mixed-pairs?tokens=False&poolData=false"
)
SHADOW_INFO_URL = "https://api.shadow.so/info"
GECKOTERMINAL_API_URL = 'https://api.geckoterminal.com/api/v2'

def calculate_volatility_metrics(candles):
    """Calculate volatility metrics from OHLCV candles using standard deviation
    Each candle is [timestamp, open, high, low, close, volume]
    
    Example calculation:
    For prices: $100, $102, $99, $101
    Mean = $100.50
    Deviations = -0.5, 1.5, -1.5, 0.5
    Squared deviations = 0.25, 2.25, 2.25, 0.25
    Average = 1.25
    Standard deviation = √1.25 ≈ 1.118
    As percentage of current price = (1.118/101) * 100 ≈ 1.11%
    """
    if not candles or len(candles) < 2:  # Need at least 2 candles
        return None
        
    # Extract close prices (newest first)
    closes = [float(candle[4]) for candle in candles]  # close is at index 4
    current_close = closes[0]  # most recent close
    
    mean_price = sum(closes) / len(closes)
    
    deviations = [price - mean_price for price in closes]
    
    squared_deviations = [dev ** 2 for dev in deviations]
    
    variance = sum(squared_deviations) / len(squared_deviations)
    
    std_dev = variance ** 0.5
    
    volatility_percentage = (std_dev / current_close) * 100 if current_close > 0 else 0
    HOURS_IN_YEAR = 24 * 365
    
    # Also keep track of simple high/low for reference
    period_high = max(closes)
    period_low = min(closes)
    
    # Return detailed metrics for analysis
    return {
        'high': period_high,
        'low': period_low,
        'current_price': current_close,
        'mean_price': mean_price,
        'std_dev': round(std_dev, 6),
        'volatility_percentage': round(volatility_percentage, 4),  # 4 decimal places for percentage
        'debug': {
            'num_samples': len(closes),
            'variance': round(variance, 6)
        }
    }

def fetch_geckoterminal_volatility(pool_address):
    """Fetch and calculate 7-day volatility metrics from GeckoTerminal OHLCV data"""
    try:
        url = f"{GECKOTERMINAL_API_URL}/networks/sonic/pools/{pool_address}/ohlcv/hour"
        params = {
            'aggregate': '1',
            'limit': '168'  # 7 days worth of hourly candles
        }
        
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        
        # Check if we have valid OHLCV data
        ohlcv_list = data.get('data', {}).get('attributes', {}).get('ohlcv_list', [])
        if not ohlcv_list:
            logger.warning(f"No OHLCV data found for pool {pool_address}")
            return None
            
        # Convert the OHLCV data to our format and ensure all values are valid
        # GeckoTerminal format: [timestamp, open, high, low, close, volume]
        candles = []
        for candle in ohlcv_list:
            try:
                if all(x is not None for x in candle):  # ensure no None values
                    candles.append([float(x) for x in candle])
            except (TypeError, ValueError):
                continue
                
        if not candles:
            logger.warning(f"No valid candle data for pool {pool_address}")
            return None
            
        # Sort candles by timestamp (newest first)
        candles.sort(key=lambda x: x[0], reverse=True)
            
        # Calculate volatility metrics
        metrics = calculate_volatility_metrics(candles)
        if not metrics:
            return None
            
        # Construct response with detailed metrics - no volume structure
        volatility = {
            'current_price': metrics['current_price'],
            'price_range': {
                'high': metrics['high'],
                'low': metrics['low'],
                'range': metrics['high'] - metrics['low'],
                'mid_price': metrics['mean_price'],
                'volatility_percentage': metrics['volatility_percentage'],
                'std_dev': metrics['std_dev'],
                'metrics': {
                    'num_samples': metrics['debug']['num_samples'],
                    'variance': metrics['debug']['variance']
                }
            }
        }
        
        return volatility
        
    except Exception as e:
        logger.warning(f"Failed to fetch GeckoTerminal data for pool {pool_address}: {e}")
        return None

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

def calculate_expected_lp_apr(pools, total_votes):
    """
    Calculate expected LP APR based on vote allocation:
    expected_lp_apr = (pool_votes / total_votes) * next_epoch_emissions_usd * 52 / pool_tvl * 100
    
    Args:
        pools (list): List of pool data dictionaries
        total_votes (float): Total votes for the period
    
    Returns:
        list: Updated pools list with expected_lp_apr field
    """
    try:
        # Fetch Shadow info data
        response = requests.get(SHADOW_INFO_URL, timeout=30)
        response.raise_for_status()
        info_data = response.json()
        
        # Extract the next epoch emissions data
        next_epoch_emissions = info_data.get('nextEpochEmissions', 0)
        next_epoch_emissions_usd = info_data.get('nextEpochEmissionsUSD', 0)
        shadow_price_usd = info_data.get('shadowPriceUSD', 0)
        current_period = info_data.get('currentPeriod', 0)
        
        logger.info(f"Next epoch emissions: {next_epoch_emissions:.2f} SHADOW (${next_epoch_emissions_usd:.2f})")
        logger.info(f"SHADOW price: ${shadow_price_usd:.4f}")
        logger.info(f"Current period: {current_period}")
        
        # Calculate expected LP APR for each pool
        updated_pools = []
        for pool in pools:
            pool_votes = pool.get('pool_votes_period', 0)
            pool_tvl = pool.get('tvl', 0)
            
            # Calculate vote percentage
            vote_percentage = pool_votes / total_votes if total_votes > 0 else 0
            
            # Calculate expected emissions for this pool
            pool_emissions_usd = vote_percentage * next_epoch_emissions_usd
            pool_emissions_shadow = vote_percentage * next_epoch_emissions
            
            # Calculate expected APR (annualized: *52 weeks, percentage: *100)
            expected_lp_apr = 0
            if pool_tvl > 0:
                expected_lp_apr = (pool_emissions_usd * 52 / pool_tvl) * 100
            
            # Add to pool data
            updated_pool = pool.copy()
            updated_pool['vote_percentage'] = vote_percentage * 100  # Convert to percentage
            updated_pool['expected_emissions_shadow'] = pool_emissions_shadow
            updated_pool['expected_emissions_usd'] = pool_emissions_usd
            updated_pool['expected_lp_apr'] = expected_lp_apr
            updated_pool['shadow_price_usd'] = shadow_price_usd
            updated_pools.append(updated_pool)
        
        return updated_pools
    except Exception as e:
        logger.error(f"❌ Failed to calculate expected LP APR: {e}")
        # Return original pools if there's an error
        return pools

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

def fetch_pools_from_api(skip_volatility=False):
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
                "tvl": p.get("tvl", 0),
                "lp_apr": p.get("lpApr", 0),
                "stats": {
                    "last_24h_vol": stats.get("last_24h_vol", 0),
                    "last_24h_fees": stats.get("last_24h_fees", 0),
                    "last_7d_vol": stats.get("last_7d_vol", 0),
                    "last_7d_fees": stats.get("last_7d_fees", 0)
                },
                "fee_last_7d_usd": stats.get("last_7d_fees", 0),
                "vol_last_7d": stats.get("last_7d_vol", 0),
                "bribes_usd": p.get("voteBribesUsd", 0),
                "tvl": p.get("tvl", 0),
                "lp_apr": p.get("lpApr", 0)
            }
            
            # Get volatility data from GeckoTerminal with rate limiting
            if not skip_volatility:
                try:
                    pool_addr = entry["pool"]
                    # Rate limit: sleep 2 seconds between requests (30 requests/minute)
                    time.sleep(2)
                    logger.info(f"Fetching volatility data for pool {entry['symbol']} ({pool_addr})")
                    volatility_data = fetch_geckoterminal_volatility(pool_addr)
                    if volatility_data:
                        entry["volatility"] = volatility_data
                except Exception as e:
                    logger.warning(f"Failed to fetch volatility data for pool {entry['pool']}: {e}")
            
            output.append(entry)
        return output
    except Exception as e:
        logger.error(f"❌ Failed to fetch pools from API: {e}")
        return []

def fetch_votes(period=None, skip_volatility=False):
    """Fetch pools from API and votes for the given period, return dashboard dict"""
    if period is None:
        period = get_current_period()
        if period is None:
            logger.error("❌ Failed to get current period")
            return None

    pools = fetch_pools_from_api(skip_volatility)
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

    # Sort by votes received
    augmented.sort(key=lambda x: x.get('pool_votes_period', 0), reverse=True)
    
    # Calculate expected LP APRs based on vote allocation
    augmented_with_apr = calculate_expected_lp_apr(augmented, float(total_votes))

    output = {
        'period': period,
        'start_date': start_date.isoformat(),
        'total_votes_period': float(total_votes),
        'pools': augmented_with_apr
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

def run_fetch(period=None, historical_dashboard_path=None, skip_volatility=False):
    """
    If historical_dashboard_path is provided, update that dashboard with on-chain votes for the given period.
    Otherwise, fetch current pools/bribes from API and on-chain votes.
    
    Args:
        period (int, optional): The period to fetch data for. If None, the current period is used.
        historical_dashboard_path (str, optional): Path to existing dashboard for historical fetch.
        skip_volatility (bool, optional): If True, skip fetching volatility data to speed up the process.
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

        dashboard = fetch_votes(period, skip_volatility)
        if dashboard:
            save_votes_dashboard(dashboard, period)
            logger.info(f"✅ Dashboard for period {period} saved/overwritten.")
        else:
            logger.error(f"❌ Failed to fetch dashboard for period {period}")