
import os
import json
import requests
import logging
import datetime
from decimal import Decimal, getcontext, ROUND_HALF_UP
from web3 import Web3
from web3.exceptions import ContractLogicError
from dotenv import load_dotenv

#!/usr/bin/env python3
VOTER_ADDRESS = os.getenv("VOTER_ADDRESS")
VE_ADDRESS = os.getenv("VE_ADDRESS")
MINTER_ADDRESS = os.getenv("MINTER_ADDRESS", "0xeb018363f0a9af8f91f06fee6613a751b2a33fe5")  # Default address provided
AERO_ADDRESS = os.getenv("AERO_ADDRESS")
PAGE_SIZE = int(os.getenv("PAGE_SIZE", 200))

# Relay support
RELAY_ACCOUNT = os.getenv("RELAY_ACCOUNT")
RELAY_SUGAR_ADDRESS = os.getenv("RELAY_SUGAR_ADDRESS")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()

# Set precision for decimal calculations
getcontext().prec = 28

# Constants
RPC_URL = os.getenv("RPC_URL")
LP_SUGAR_ADDRESS = os.getenv("LP_SUGAR_ADDRESS")
REWARDS_SUGAR_ADDR = os.getenv("REWARDS_SUGAR_ADDRESS")
VOTER_ADDRESS = os.getenv("VOTER_ADDRESS")
VE_ADDRESS = os.getenv("VE_ADDRESS")
MINTER_ADDRESS = os.getenv("MINTER_ADDRESS", "0xeb018363f0a9af8f91f06fee6613a751b2a33fe5")  # Default address provided
AERO_ADDRESS = os.getenv("AERO_ADDRESS")
PAGE_SIZE = int(os.getenv("PAGE_SIZE", 200))

# Relay support
RELAY_ACCOUNT = os.getenv("RELAY_ACCOUNT")
RELAY_SUGAR_ADDRESS = os.getenv("RELAY_SUGAR_ADDRESS")
COINGECKO_SIMPLE_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"
COINGECKO_COINS_LIST_URL = "https://api.coingecko.com/api/v3/coins/list?include_platform=true"
COINGECKO_AERO_ID = os.getenv("COINGECKO_AERO_ID", "aerodrome-finance")  # Default value

# Default investment sizes for APR calculation
DEFAULT_INVESTMENT_SIZES = [1000, 10000, 50000]

# ABIs
def load_abi(name):
    """Load ABI from file"""
    with open(f"abi/aero/{name}.json") as f:
        return json.load(f)

LP_SUGAR_ABI = load_abi("LpSugar")
REWARDS_SUGAR_ABI = load_abi("RewardsSugar")
VOTER_ABI = load_abi("Voter")
MINTER_ABI = load_abi("Minter")
RELAY_SUGAR_ABI = load_abi("RelaySugar")  # Add RelaySugar ABI

ERC20_ABI = [
    {
        "constant": True,
        "inputs": [],
        "name": "symbol",
        "outputs": [{"name": "", "type": "string"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function"
    }
]

# Cache for token data
_token_decimals_cache = {}
_token_symbol_cache = {}

# Helpers
def from_wei(val):
    """Convert Wei value to Ether"""
    return Decimal(val) / Decimal(10**18)

def get_web3():
    """Initialize and return a Web3 instance"""
    if not RPC_URL:
        logger.error("❌ RPC_URL not set in environment")
        return None
    
    return Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 60}))

def save_json(data, path):
    """Save data to a JSON file"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2, default=float)
    logger.info(f"✅ Saved data to {path}")

def get_token_decimals(w3, token_addr):
    """Get token decimals, with caching"""
    key = token_addr.lower()
    if key in _token_decimals_cache:
        return _token_decimals_cache[key]
    try:
        c = w3.eth.contract(address=w3.to_checksum_address(key), abi=ERC20_ABI)
        d = c.functions.decimals().call()
    except Exception:
        d = 18
    _token_decimals_cache[key] = d
    return d

def get_token_symbol(w3, token_addr):
    """Get token symbol, with caching"""
    key = token_addr.lower()
    if key in _token_symbol_cache:
        return _token_symbol_cache[key]
    try:
        c = w3.eth.contract(address=w3.to_checksum_address(key), abi=ERC20_ABI)
        s = c.functions.symbol().call()
    except Exception:
        s = None
    _token_symbol_cache[key] = s
    return s

def get_current_epoch():
    """Get the current week from Aero - we'll use the activeperiod from Minter"""
    w3 = get_web3()
    if not w3:
        return 0
    
    try:
        minter = w3.eth.contract(
            address=w3.to_checksum_address(MINTER_ADDRESS),
            abi=MINTER_ABI
        )
        
        # Aero uses activePeriod for the current week
        period = minter.functions.activePeriod().call()
        logger.info(f"ℹ️ Current period: {period}")
        return period
    except Exception as e:
        logger.error(f"❌ Failed to get current period: {e}")
        return 0

def get_total_weight():
    """Get total weight (votes) from Voter contract"""
    w3 = get_web3()
    if not w3:
        return Decimal('0')
    
    try:
        voter = w3.eth.contract(
            address=w3.to_checksum_address(VOTER_ADDRESS),
            abi=VOTER_ABI
        )
        
        # For Aero, we use totalWeight to get the total votes/weight
        total_weight = voter.functions.totalWeight().call()
        total_weight_decimal = from_wei(total_weight)
        logger.info(f"ℹ️ Total weight: {total_weight_decimal}")
        return total_weight_decimal
    except Exception as e:
        logger.error(f"❌ Failed to get total weight: {e}")
        return Decimal('0')

def get_pool_weight(pool_addr):
    """Get weight (votes) for a specific pool"""
    w3 = get_web3()
    if not w3:
        return Decimal('0')
    
    try:
        voter = w3.eth.contract(
            address=w3.to_checksum_address(VOTER_ADDRESS),
            abi=VOTER_ABI
        )
        
        # For Aero, we use weights(pool) to get the votes/weight for a pool
        # The weights function expects the pool address, not the gauge address
        weight = voter.functions.weights(w3.to_checksum_address(pool_addr)).call()
        return from_wei(weight)
    except Exception as e:
        logger.error(f"❌ Failed to get pool weight for {pool_addr}: {e}")
        return Decimal('0')

def get_aero_price():
    """Get AERO token price from CoinGecko"""
    try:
        params = {
            'ids': COINGECKO_AERO_ID,
            'vs_currencies': 'usd'
        }
        response = requests.get(COINGECKO_SIMPLE_PRICE_URL, params=params)
        response.raise_for_status()
        data = response.json()
        
        price = Decimal(str(data.get(COINGECKO_AERO_ID, {}).get('usd', 0)))
        logger.info(f"ℹ️ AERO price: ${price}")
        return price
    except Exception as e:
        logger.error(f"❌ Failed to get AERO price: {e}")
        return Decimal('0')

def get_weekly_emissions():
    """Get weekly emissions from Minter contract"""
    w3 = get_web3()
    if not w3:
        return Decimal('0')
    
    try:
        minter = w3.eth.contract(
            address=w3.to_checksum_address(MINTER_ADDRESS),
            abi=MINTER_ABI
        )
        
        weekly_wei = minter.functions.weekly().call()
        weekly = from_wei(weekly_wei)
        logger.info(f"ℹ️ Weekly emissions: {weekly} AERO")
        return weekly
    except Exception as e:
        logger.error(f"❌ Failed to get weekly emissions: {e}")
        return Decimal('0')

def fetch_relay_votes(w3, pools):
    """Fetch relay votes data"""
    logger.info("🔍 Fetching relay votes data...")
    
    if not RELAY_ACCOUNT or not RELAY_SUGAR_ADDRESS:
        logger.warning("⚠️ Missing RELAY_ACCOUNT or RELAY_SUGAR_ADDRESS, skipping relay votes fetch")
        return {}
    
    # Create a mapping of pool addresses to symbols for quick lookup
    pool_symbols = {p.get("pool", "").lower(): p.get("symbol", "") for p in pools if "pool" in p}
    
    try:
        # Create contract instance
        relay_sugar = w3.eth.contract(
            address=w3.to_checksum_address(RELAY_SUGAR_ADDRESS),
            abi=RELAY_SUGAR_ABI
        )
        
        # Fetch all relays for the account
        relays_raw = relay_sugar.functions.all(w3.to_checksum_address(RELAY_ACCOUNT)).call()
        logger.info(f"→ Retrieved {len(relays_raw)} Relay entries")
        
        relay_totals = {}
        for raw in relays_raw:
            # Parse relay struct
            decimals_raw = raw[1]
            voting_amount_raw = raw[3]
            votes_arr = raw[6] if isinstance(raw[6], list) else []
            
            voting_amount_hr = Decimal(voting_amount_raw) / (Decimal(10) ** int(decimals_raw))
            
            # Add votes to totals
            if voting_amount_hr > 0:
                for (pool_addr, weight_raw) in votes_arr:
                    pool_l = pool_addr.lower()
                    weight_hr = Decimal(weight_raw) / (Decimal(10) ** 18)
                    relay_totals[pool_l] = relay_totals.get(pool_l, Decimal(0)) + weight_hr
        
        logger.info(f"✅ Processed relay votes for {len(relay_totals)} pools")
        return relay_totals
    
    except Exception as e:
        logger.error(f"❌ Error fetching relay votes: {e}")
        return {}

def fetch_coingecko_token_ids(tokens):
    """Map token addresses to CoinGecko IDs"""
    logger.info("Fetching CoinGecko token IDs...")
    url = COINGECKO_COINS_LIST_URL
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    all_coins = resp.json()
    
    mapping = {}
    missing = set(tokens)
    for coin in all_coins:
        platforms = coin.get("platforms") or {}
        base_addr = platforms.get("base")
        if base_addr:
            base_addr_lc = base_addr.lower()
            if base_addr_lc in tokens:
                mapping[base_addr_lc] = coin["id"]
                missing.discard(base_addr_lc)
    
    logger.info(f"Mapped {len(mapping)} of {len(tokens)} tokens to CoinGecko IDs")
    return mapping

def fetch_prices_from_coingecko(token_to_id):
    """Fetch current USD prices from CoinGecko"""
    unique_ids = list(set(token_to_id.values()))
    prices = {}
    
    CHUNK = 80
    for i in range(0, len(unique_ids), CHUNK):
        chunk_ids = unique_ids[i:i+CHUNK]
        ids_param = ",".join(chunk_ids)
        params = {
            "ids": ids_param,
            "vs_currencies": "usd"
        }
        try:
            resp = requests.get(COINGECKO_SIMPLE_PRICE_URL, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            
            for coin_id, price_info in data.items():
                price = price_info.get("usd")
                if price is None:
                    continue
                
                for contract, cid in token_to_id.items():
                    if cid == coin_id:
                        prices[contract] = Decimal(str(price))
        except Exception as e:
            logger.warning(f"CoinGecko API error: {e}")
    
    return prices

def fetch_all_pools():
    """Fetch all pools from LpSugar contract"""
    w3 = get_web3()
    if not w3:
        return []
    
    logger.info("🔍 Fetching all pools via LpSugar...")
    lp_sugar = w3.eth.contract(
        address=w3.to_checksum_address(LP_SUGAR_ADDRESS),
        abi=LP_SUGAR_ABI
    )
    
    # Extract field names from ABI
    fn_abi = next((item for item in lp_sugar.abi if item.get("name") == "all" and item.get("type") == "function"), None)
    if not fn_abi:
        logger.error("❌ Could not find 'all' function in LpSugar ABI")
        return []
    
    field_names = [c["name"] for c in fn_abi["outputs"][0]["components"]]
    
    # Fetch all pools in batches
    offset = 0
    all_pools = []
    while True:
        try:
            batch = lp_sugar.functions.all(PAGE_SIZE, offset).call()
            if not batch:
                break
            all_pools.extend(batch)
            offset += PAGE_SIZE
        except ContractLogicError:
            break
    
    # Format pools
    formatted_pools = []
    for entry in all_pools:
        pool_dict = {}
        for name, val in zip(field_names, entry):
            # Convert bytes to hex strings
            if isinstance(val, (bytes, bytearray)):
                pool_dict[name] = "0x" + val.hex()
            else:
                pool_dict[name] = val
        formatted_pools.append(pool_dict)
    
    logger.info(f"✅ Retrieved {len(formatted_pools)} pools")
    return formatted_pools

def filter_votable_pools(pools):
    """Filter pools to only include votable ones"""
    logger.info("Filtering votable pools...")
    zero_addr = "0x0000000000000000000000000000000000000000"
    votable = [
        p for p in pools
        if p.get("gauge", zero_addr).lower() != zero_addr
        and p.get("gauge_alive", False) is True
    ]
    
    # Add pool address to each pool for vote weight calculation
    for p in votable:
        if "pool" not in p:
            p["pool"] = p.get("lp", zero_addr)
    
    # Sort by liquidity
    votable.sort(key=lambda x: int(x["liquidity"]), reverse=True)
    
    logger.info(f"→ {len(votable)} votable pools after filtering")
    return votable

def enrich_pools(w3, pools):
    """Add token symbols to pools and calculate TVL"""
    logger.info("Enriching pools with token symbols and TVL...")
    
    # Get token addresses and map to CoinGecko IDs
    tokens = set()
    for p in pools:
        t0 = p.get("token0", "").lower()
        t1 = p.get("token1", "").lower()
        if w3.is_address(t0):
            tokens.add(w3.to_checksum_address(t0).lower())
        if w3.is_address(t1):
            tokens.add(w3.to_checksum_address(t1).lower())
    
    # Fetch CoinGecko IDs and prices
    token_to_id = fetch_coingecko_token_ids(tokens)
    token_prices = fetch_prices_from_coingecko(token_to_id)
    
    enriched_pools = []
    zero_addr = "0x0000000000000000000000000000000000000000"
    
    for pool in pools:
        # Ensure we have symbol
        symbol = pool.get("symbol", "") or ""
        if not symbol or symbol.lower().startswith("0x"):
            token0 = pool.get("token0", zero_addr)
            token1 = pool.get("token1", zero_addr)
            
            sym0 = get_token_symbol(w3, token0) or token0[:6]
            sym1 = get_token_symbol(w3, token1) or token1[:6]
            symbol = f"{sym0}/{sym1}"
            pool["symbol"] = symbol
        
        # Calculate TVL in USD
        token0 = pool.get("token0", "").lower()
        token1 = pool.get("token1", "").lower()
        reserve0 = Decimal(str(pool.get("reserve0", 0)))
        reserve1 = Decimal(str(pool.get("reserve1", 0)))
        
        token0_price = token_prices.get(token0, Decimal(0))
        token1_price = token_prices.get(token1, Decimal(0))
        
        # Convert reserves to human readable with decimals
        dec0 = get_token_decimals(w3, token0)
        dec1 = get_token_decimals(w3, token1)
        
        reserve0_human = reserve0 / (Decimal(10) ** dec0)
        reserve1_human = reserve1 / (Decimal(10) ** dec1)
        
        # Calculate USD value
        token0_usd = reserve0_human * token0_price
        token1_usd = reserve1_human * token1_price
        tvl_usd = token0_usd + token1_usd
        
        # Add to pool data
        pool["tvl_usd"] = float(tvl_usd)
        pool["token0_price"] = float(token0_price)
        pool["token1_price"] = float(token1_price)
        pool["reserve0_human"] = float(reserve0_human)
        pool["reserve1_human"] = float(reserve1_human)
        
        enriched_pools.append(pool)
    
    # Sort by TVL
    enriched_pools.sort(key=lambda x: x.get("tvl_usd", 0), reverse=True)
    
    logger.info(f"Enriched {len(enriched_pools)} pools with TVL data")
    return enriched_pools

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
    current_tvl = Decimal(str(pool_data.get('tvl_usd', 0)))
    
    if current_tvl <= Decimal('0.01'):
        return Decimal('0')
    
    # Calculate new TVL with our investment
    new_tvl = current_tvl + Decimal(str(investment_amount))
    
    # Calculate our ownership percentage
    ownership_percentage = Decimal(str(investment_amount)) / new_tvl
    
    # Calculate our share of rewards
    our_rewards = rewards_usd * ownership_percentage
    
    # Calculate annualized APR (52 weeks in a year)
    apr = (our_rewards * 52 / Decimal(str(investment_amount))) * 100
    
    return apr.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

def calculate_lp_data(pools, investment_sizes=None):
    """
    Calculate LP APR data for all pools at different investment sizes
    
    This function calculates APR based on the vote weights for each pool,
    including relay votes to get a more accurate picture.
    
    Args:
        pools: List of pool data (each pool should have a 'pool' field with the pool address)
        investment_sizes: List of investment amounts to calculate APR for
        
    Returns:
        Updated pools list with LP APR data
    """
    if investment_sizes is None:
        investment_sizes = DEFAULT_INVESTMENT_SIZES
    
    w3 = get_web3()
    if not w3:
        logger.error("❌ Failed to connect to RPC")
        return []
    
    # Get current period
    current_period = get_current_epoch()
    
    # Get total weight
    total_weight = get_total_weight()
    
    # Get relay votes
    relay_votes = fetch_relay_votes(w3, pools)
    
    # Get weekly emissions and price
    weekly_emissions = get_weekly_emissions()
    aero_price = get_aero_price()
    weekly_emissions_usd = weekly_emissions * aero_price
    
    logger.info(f"ℹ️ Weekly emissions: {weekly_emissions} AERO (${weekly_emissions_usd})")
    
    updated_pools = []
    for pool in pools:
        # Use pool address for voting weights, not gauge address
        pool_address = pool.get('pool')
        
        # Get on-chain weight for this pool
        weight = get_pool_weight(pool_address)
        
        # Get relay votes for this pool
        relay_weight = relay_votes.get(pool_address.lower(), Decimal('0'))
        
        # Add relay votes to regular votes for total weight
        total_pool_weight = weight + relay_weight
        
        # Calculate weight percentage based on combined weight
        weight_pct = (total_pool_weight / total_weight * 100) if total_weight > 0 else Decimal('0')
        
        # Calculate rewards based on combined weight allocation
        rewards = (total_pool_weight / total_weight) * weekly_emissions_usd if total_weight > 0 else Decimal('0')
        
        # Calculate base APR
        tvl_usd = Decimal(str(pool.get('tvl_usd', 0)))
        
        # Calculate APR - prevent division by zero
        if tvl_usd <= Decimal('0.01'):  # Very small TVL
            base_apr = Decimal('0')
        else:
            base_apr = (rewards * 52 / tvl_usd * 100)
        
        # Calculate APR at different investment sizes
        apr_by_investment = {}
        for size in investment_sizes:
            apr_by_investment[str(size)] = calculate_apr_at_investment_size(pool, size, rewards)
        
        # Update pool with calculated data
        updated_pool = pool.copy()
        updated_pool.update({
            'weight': float(weight),
            'relay_votes': float(relay_weight),
            'total_weight': float(total_pool_weight),
            'weight_pct': float(weight_pct),
            'weekly_rewards': float(rewards),
            'apr': float(base_apr),
            'apr_by_investment': {str(size): float(apr) for size, apr in apr_by_investment.items()}
        })
        
        updated_pools.append(updated_pool)
    
    # Sort by APR, descending
    updated_pools.sort(key=lambda x: x.get('apr', 0), reverse=True)
    
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
    
    current_period = get_current_epoch()
    date_str = datetime.datetime.now().strftime("%Y%m%d")
    
    # Create dashboard data
    dashboard = {
        'period': current_period,
        'date': date_str,
        'investment_sizes': [float(size) for size in investment_sizes],
        'pools': lp_data
    }
    
    # Save to files
    dated_path = f'lp_dashboard/aero/lp_dashboard_{date_str}.json'
    current_path = f'lp_dashboard/aero/lp_dashboard.json'
    
    # Save files
    save_json(dashboard, dated_path)
    save_json(dashboard, current_path)
    
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
    
    # Limit to top N pools
    display_pools = pools[:min(top_n, len(pools))]
    
    # Format investment sizes for display
    investment_str = [f"${size/1000}k" for size in investment_sizes]
    
    # Print header
    print("\n================ AERO LP DASHBOARD ================")
    print(f"Date: {datetime.datetime.now().strftime('%Y-%m-%d')}")
    print(f"Showing top {len(display_pools)} pools by APR")
    print("--------------------------------------------------")
    
    # Print column headers
    header = f"{'Pool':20} {'TVL':>12} {'Weight':>10} {'Relay':>10} {'APR':>8}"
    for size_str in investment_str:
        header += f" {f'APR @ {size_str}':>10}"
    print(header)
    print("--------------------------------------------------")
    
    # Print each pool
    for pool in display_pools:
        symbol = pool.get('symbol', '')[:18].ljust(18)
        
        # Format TVL
        tvl_val = pool.get('tvl_usd', 0)
        if tvl_val < 1000000:  # Less than 1M
            tvl = f"${tvl_val/1000:.2f}K".rjust(12)
        else:  # 1M or more
            tvl = f"${tvl_val/1000000:.2f}M".rjust(12)
        
        # Format weights
        weight = f"{pool.get('weight', 0):.2f}".rjust(10)
        relay_votes = f"{pool.get('relay_votes', 0):.2f}".rjust(10)
        apr = f"{pool.get('apr', 0):.2f}%".rjust(8)
        
        line = f"{symbol} {tvl} {apr}"
        
        # Add APR at different investment sizes
        apr_by_inv = pool.get('apr_by_investment', {})
        for size in investment_sizes:
            size_apr = apr_by_inv.get(str(size), 0)
            line += f" {f'{size_apr:.2f}%'.rjust(10)}"
        
        print(line)
    
    print("==================================================")
    print("\n")

def run_fetch_lp_data(investment_sizes=None, display=True, save=True, top_n=30):
    """
    Main function to fetch and process LP data
    
    Args:
        investment_sizes: List of investment amounts to calculate APR for
        display: Whether to display the dashboard in the terminal
        save: Whether to save the dashboard to file
        top_n: Number of top pools to process
        
    Returns:
        Processed LP data
    """
    if investment_sizes is None:
        investment_sizes = DEFAULT_INVESTMENT_SIZES
    
    logger.info(f"Starting Aero LP Dashboard generation...")
    logger.info(f"Investment sizes: {investment_sizes}")
    
    w3 = get_web3()
    if not w3:
        logger.error("❌ Failed to connect to RPC")
        return None
    
    # Step 1: Fetch all pools
    all_pools = fetch_all_pools()
    if not all_pools:
        logger.error("❌ No pools found")
        return None
    
    # Step 2: Filter votable pools
    votable_pools = filter_votable_pools(all_pools)
    
    # Step 3: Use only top N pools for performance
    top_pools = votable_pools[:top_n]
    logger.info(f"→ Using top {len(top_pools)} pools by TVL")
    
    # Step 4: Enrich pools with symbols and TVL
    enriched_pools = enrich_pools(w3, top_pools)
    
    # Step 5: Calculate LP APR data
    lp_data = calculate_lp_data(enriched_pools, investment_sizes)
    
    # Step 6: Display dashboard
    if display:
        display_lp_dashboard(lp_data, investment_sizes, top_n)
    
    # Step 7: Save dashboard
    if save:
        save_lp_dashboard(lp_data, investment_sizes)
    
    logger.info("✅ LP Dashboard generation complete!")
    return lp_data

if __name__ == "__main__":
    run_fetch_lp_data()
