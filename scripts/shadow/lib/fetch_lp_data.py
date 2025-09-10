
import os
import json
import logging
import requests
from decimal import Decimal, getcontext, ROUND_HALF_UP
from web3 import Web3
from dotenv import load_dotenv
import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


load_dotenv()


getcontext().prec = 28


RPC_URL = os.getenv('SHADOW_RPC_URL')
VOTER_ADDRESS = os.getenv('SHADOW_VOTER_ADDRESS')
VOTER_ABI_PATH = os.getenv('VOTER_ABI_PATH', 'abi/shadow/Voter.json')
SHADOW_API_URL = os.getenv(
    "SHADOW_API_URL",
    "https://api.shadow.so/mixed-pairs?tokens=False&poolData=false"
)
SHADOW_INFO_URL = "https://api.shadow.so/info"


DEFAULT_INVESTMENT_SIZES = [1000, 10000, 50000]  

def from_wei(val):
    """Convert Wei value to Ether"""
    return Decimal(val) / Decimal(10**18)

def get_web3_and_contract():
    """Initialize Web3 and contract connection"""
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

def get_current_period():
    """Get the current voting period"""
    _, contract = get_web3_and_contract()
    if not contract:
        return None
    return contract.functions.getPeriod().call() + 1  

def get_total_votes_period(period):
    """Get total votes for a specific period"""
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
    """Get votes for a specific pool in a specific period"""
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

def fetch_shadow_info():
    """Fetch global Shadow protocol information"""
    try:
        response = requests.get(SHADOW_INFO_URL, timeout=30)
        response.raise_for_status()
        info_data = response.json()
        
        
        
        current_period = info_data.get('currentPeriod', 0)
        next_epoch_emissions = Decimal(str(info_data.get('nextEpochEmissions', 0)))
        next_epoch_emissions_usd = Decimal(str(info_data.get('nextEpochEmissionsUSD', 0)))
        current_epoch_emissions = Decimal(str(info_data.get('currentEpochEmissions', 0)))
        current_epoch_emissions_usd = Decimal(str(info_data.get('currentEpochEmissionsUSD', 0)))
        shadow_price_usd = Decimal(str(info_data.get('shadowPriceUSD', 0)))
        
        logger.info(f"Current epoch (votes being applied now): {current_period}")
        logger.info(f"Next epoch (votes being collected now): {current_period + 1}")
        logger.info(f"SHADOW price: ${shadow_price_usd:.4f}")
        logger.info(f"Current epoch emissions: {current_epoch_emissions:.2f} SHADOW (${current_epoch_emissions_usd:.2f})")
        logger.info(f"Next epoch emissions: {next_epoch_emissions:.2f} SHADOW (${next_epoch_emissions_usd:.2f})")
        
        return {
            'current_period': current_period,
            'shadow_price_usd': shadow_price_usd,
            'current_epoch_emissions': current_epoch_emissions,
            'current_epoch_emissions_usd': current_epoch_emissions_usd,
            'next_epoch_emissions': next_epoch_emissions,
            'next_epoch_emissions_usd': next_epoch_emissions_usd
        }
    except Exception as e:
        logger.error(f"❌ Failed to fetch Shadow info: {e}")
        return None

def fetch_pools_from_api(top_n=30):
    """
    Fetch pools data from Shadow API
    
    Args:
        top_n: Number of top pools by TVL to return
        
    Returns:
        List of pool data dictionaries
    """
    try:
        response = requests.get(SHADOW_API_URL)
        response.raise_for_status()
        data = response.json()
        pools = data.get("pairs", [])
        logger.info(f"🔍 Fetched {len(pools)} pools from Shadow API")
        
        
        def is_active(pool):
            v2 = pool.get("gaugeV2") or {}
            if v2.get("isAlive", False):
                return True
            g = pool.get("gauge") or {}
            return bool(g.get("isAlive", False))
            
        active_pools = [p for p in pools if is_active(p)]
        logger.info(f"→ {len(active_pools)} active pools after filtering")
        
        
        sorted_pools = sorted(
            active_pools,
            key=lambda p: p.get("tvl", 0),
            reverse=True
        )
        
        
        top_pools = sorted_pools[:top_n]
        logger.info(f"→ Using top {len(top_pools)} pools by TVL")
        
        output = []
        for p in top_pools:
            stats = p.get("stats", {})
            entry = {
                "pool": p.get("id"),
                "symbol": p.get("symbol"),
                "tvl": p.get("tvl", 0),
                "lp_apr": p.get("lpApr", 0),
                "stats": {
                    "last_24h_volume": stats.get("last_24h_vol", 0),
                    "last_24h_fees": stats.get("last_24h_fees", 0),
                    "last_7d_volume": stats.get("last_7d_vol", 0),
                    "last_7d_fees": stats.get("last_7d_fees", 0)
                }
            }
            output.append(entry)
        return output
    except Exception as e:
        logger.error(f"❌ Failed to fetch pools from API: {e}")
        return []

def calculate_apr_at_investment_size(pool_data, investment_amount, rewards_usd):
    """
    Calculate APR for a specific investment amount
    
    Args:
        pool_data: Pool information including TVL
        investment_amount: Amount to invest in USD
        rewards_usd: Rewards in USD based on vote allocation
        
    Returns:
        APR as a percentage
    """
    
    current_tvl = Decimal(str(pool_data.get('tvl', 0)))
    
    
    if current_tvl <= 0:
        return 0
    
    
    new_tvl = current_tvl + Decimal(str(investment_amount))
    
    
    ownership_percentage = Decimal(str(investment_amount)) / new_tvl
    
    
    our_rewards = rewards_usd * ownership_percentage
    
    
    apr = (our_rewards * 52 / Decimal(str(investment_amount))) * 100
    
    return apr.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

def calculate_lp_data(pools, investment_sizes=None):
    """
    Calculate LP APR data for all pools at different investment sizes
    
    Args:
        pools: List of pool data
        investment_sizes: List of investment amounts to calculate APR for
        
    Returns:
        Updated pools list with LP APR data
    """
    if investment_sizes is None:
        investment_sizes = DEFAULT_INVESTMENT_SIZES
    
    
    shadow_info = fetch_shadow_info()
    if not shadow_info:
        logger.error("❌ Failed to fetch Shadow info, using fallback values")
        shadow_info = {
            'current_period': get_current_period(),
            'shadow_price_usd': Decimal('0'),
            'current_epoch_emissions': Decimal('0'),
            'current_epoch_emissions_usd': Decimal('0'),
            'next_epoch_emissions': Decimal('0'),
            'next_epoch_emissions_usd': Decimal('0')
        }
    
    
    current_epoch = shadow_info['current_period']
    
    next_epoch = current_epoch + 1
    
    
    total_votes_current_epoch = get_total_votes_period(current_epoch)
    total_votes_next_epoch = get_total_votes_period(next_epoch)
    
    logger.info(f"ℹ️ Total votes for current epoch {current_epoch}: {total_votes_current_epoch}")
    logger.info(f"ℹ️ Total votes for next epoch {next_epoch}: {total_votes_next_epoch}")
    
    
    updated_pools = []
    for pool in pools:
        pool_address = pool.get('pool')
        
        
        current_epoch_votes = get_pool_votes_period(pool_address, current_epoch)
        next_epoch_votes = get_pool_votes_period(pool_address, next_epoch)
        
        
        current_epoch_vote_pct = (current_epoch_votes / total_votes_current_epoch * 100) if total_votes_current_epoch > 0 else Decimal('0')
        next_epoch_vote_pct = (next_epoch_votes / total_votes_next_epoch * 100) if total_votes_next_epoch > 0 else Decimal('0')
        
        
        current_epoch_rewards = (current_epoch_votes / total_votes_current_epoch) * shadow_info['current_epoch_emissions_usd'] if total_votes_current_epoch > 0 else Decimal('0')
        next_epoch_rewards = (next_epoch_votes / total_votes_next_epoch) * shadow_info['next_epoch_emissions_usd'] if total_votes_next_epoch > 0 else Decimal('0')
        
        
        tvl = Decimal(str(pool.get('tvl', 0)))
        current_epoch_apr = (current_epoch_rewards * 52 / tvl * 100) if tvl > 0 else Decimal('0')
        next_epoch_apr = (next_epoch_rewards * 52 / tvl * 100) if tvl > 0 else Decimal('0')
        
        
        apr_by_investment = {}
        for size in investment_sizes:
            apr_by_investment[str(size)] = calculate_apr_at_investment_size(pool, size, next_epoch_rewards)
        
        
        updated_pool = pool.copy()
        updated_pool.update({
            'current_epoch': {
                'epoch_number': current_epoch,
                'votes': float(current_epoch_votes),
                'vote_pct': float(current_epoch_vote_pct),
                'rewards': float(current_epoch_rewards),
                'apr': float(current_epoch_apr)
            },
            'next_epoch': {
                'epoch_number': next_epoch,
                'votes': float(next_epoch_votes),
                'vote_pct': float(next_epoch_vote_pct),
                'rewards': float(next_epoch_rewards),
                'apr': float(next_epoch_apr)
            },
            'apr_by_investment': {k: float(v) for k, v in apr_by_investment.items()}
        })
        
        updated_pools.append(updated_pool)
    
    
    updated_pools.sort(key=lambda x: x.get('next_epoch', {}).get('apr', 0), reverse=True)
    
    return updated_pools

def save_lp_dashboard(lp_data, investment_sizes=None):
    """
    Save LP data to dashboard files
    
    Args:
        lp_data: Processed LP data with APRs
        investment_sizes: List of investment amounts used for APR calculation
    """
    if investment_sizes is None:
        investment_sizes = DEFAULT_INVESTMENT_SIZES
    
    current_period = get_current_period()
    date_str = datetime.datetime.now().strftime("%Y%m%d")
    
    
    dashboard = {
        'period': current_period,
        'date': date_str,
        'investment_sizes': investment_sizes,
        'pools': lp_data
    }
    
    
    dated_path = f'lp_dashboard/shadow/lp_dashboard_{date_str}.json'
    current_path = f'lp_dashboard/shadow/lp_dashboard.json'
    
    
    os.makedirs(os.path.dirname(dated_path), exist_ok=True)
    
    
    with open(dated_path, 'w') as f:
        json.dump(dashboard, f, indent=2)
    logger.info(f"✅ Saved LP dashboard to {dated_path}")
    
    
    with open(current_path, 'w') as f:
        json.dump(dashboard, f, indent=2)
    logger.info(f"✅ Saved LP dashboard to {current_path}")
    
    return dashboard

def display_lp_dashboard(pools, investment_sizes=None, top_n=30):
    """
    Display LP dashboard in a readable format
    
    Args:
        pools: List of pool data with APR information
        investment_sizes: List of investment amounts
        top_n: Number of top pools to display
    """
    if investment_sizes is None:
        investment_sizes = DEFAULT_INVESTMENT_SIZES
    
    
    investment_str = [f"${size/1000}k" for size in investment_sizes]
    
    
    print("\n================ LP DASHBOARD ================")
    print(f"Date: {datetime.datetime.now().strftime('%Y-%m-%d')}")
    print(f"Showing top {top_n} pools by next epoch APR")
    print("----------------------------------------------")
    
    
    header = f"{'Pool':20} {'TVL':>12} {'Curr APR':>10} {'Next APR':>10}"
    for size_str in investment_str:
        header += f" {f'APR @ {size_str}':>10}"
    print(header)
    print("----------------------------------------------")
    
    
    for i, pool in enumerate(pools[:top_n]):
        symbol = pool.get('symbol', '')[:18].ljust(18)
        tvl = f"${pool.get('tvl', 0)/1000000:.2f}M".rjust(12)
        curr_apr = f"{pool.get('current_epoch', {}).get('apr', 0):.2f}%".rjust(10)
        next_apr = f"{pool.get('next_epoch', {}).get('apr', 0):.2f}%".rjust(10)
        
        line = f"{symbol} {tvl} {curr_apr} {next_apr}"
        
        
        apr_by_inv = pool.get('apr_by_investment', {})
        for size in investment_sizes:
            size_apr = apr_by_inv.get(str(size), 0)
            line += f" {f'{size_apr:.2f}%'.rjust(10)}"
        
        print(line)
    
    print("==============================================\n")

def run_fetch_lp_data(investment_sizes=None, display=True, save=True, top_n=30):
    """
    Main function to fetch LP data and calculate APRs
    
    Args:
        investment_sizes: List of investment amounts to calculate APR for
        display: Whether to display the dashboard
        save: Whether to save the dashboard to files
        top_n: Number of top pools by TVL to fetch and display
        
    Returns:
        LP dashboard data
    """
    if investment_sizes is None:
        investment_sizes = DEFAULT_INVESTMENT_SIZES
    
    
    pools = fetch_pools_from_api(top_n)
    if not pools:
        logger.error("❌ No pools fetched from API")
        return None
    
    
    lp_data = calculate_lp_data(pools, investment_sizes)
    
    
    if save:
        save_lp_dashboard(lp_data, investment_sizes)
    
    
    if display:
        display_lp_dashboard(lp_data, investment_sizes, top_n)
    
    return lp_data

if __name__ == "__main__":
    run_fetch_lp_data()
