#!/usr/bin/env python3
# filepath: d:\Pyth\pythfarms\scripts\aero\lib\fetch_lp_data_previous_epoch.py

import os
import json
import time
import requests
import logging
import datetime
from decimal import Decimal, getcontext, ROUND_HALF_UP
from web3 import Web3
from web3.exceptions import ContractLogicError
from dotenv import load_dotenv

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
MINTER_ADDRESS = os.getenv("MINTER_ADDRESS", "0xeb018363f0a9af8f91f06fee6613a751b2a33fe5")
AERO_ADDRESS = os.getenv("AERO_ADDRESS")
PAGE_SIZE = int(os.getenv("PAGE_SIZE", 200))
MORALIS_API_KEY = os.getenv("MORALIS_API_KEY")

# Relay support
RELAY_ACCOUNT = os.getenv("RELAY_ACCOUNT")
RELAY_SUGAR_ADDRESS = os.getenv("RELAY_SUGAR_ADDRESS")

# CoinGecko URLs
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
RELAY_SUGAR_ABI = load_abi("RelaySugar")

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

def get_token_decimals(w3, token_addr, block_number=None):
    """Get token decimals, with caching"""
    key = token_addr.lower()
    if key in _token_decimals_cache:
        return _token_decimals_cache[key]
    try:
        c = w3.eth.contract(address=w3.to_checksum_address(key), abi=ERC20_ABI)
        if block_number:
            d = c.functions.decimals().call(block_identifier=block_number)
        else:
            d = c.functions.decimals().call()
    except Exception:
        d = 18
    _token_decimals_cache[key] = d
    return d

def get_token_symbol(w3, token_addr, block_number=None):
    """Get token symbol, with caching"""
    key = token_addr.lower()
    if key in _token_symbol_cache:
        return _token_symbol_cache[key]
    try:
        c = w3.eth.contract(address=w3.to_checksum_address(key), abi=ERC20_ABI)
        if block_number:
            s = c.functions.symbol().call(block_identifier=block_number)
        else:
            s = c.functions.symbol().call()
    except Exception:
        s = None
    _token_symbol_cache[key] = s
    return s

def get_previous_epoch_timestamp():
    """
    Calculate the timestamp for 10 minutes before the previous epoch change
    
    Epochs change on Thursday at 00:00 UTC
    We want Wednesday at 23:50 UTC of the previous week
    """
    # Get current time
    now = datetime.datetime.now(datetime.timezone.utc)
    
    # Calculate last Thursday
    days_since_thursday = (now.weekday() - 3) % 7
    last_thursday = now - datetime.timedelta(days=days_since_thursday)
    
    # If it's Thursday and before epoch change, use the previous Thursday
    if now.weekday() == 3 and now.hour < 1:
        last_thursday = last_thursday - datetime.timedelta(days=7)
    
    # Get 10 minutes before midnight on the Wednesday before
    previous_epoch_end = datetime.datetime(
        year=last_thursday.year,
        month=last_thursday.month,
        day=last_thursday.day,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
        tzinfo=datetime.timezone.utc
    ) - datetime.timedelta(minutes=10)
    
    logger.info(f"Previous epoch snapshot time: {previous_epoch_end.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    
    # Return as unix timestamp
    return int(previous_epoch_end.timestamp())

def get_block_by_timestamp(timestamp):
    """
    Get the closest block number for a given timestamp using Moralis API
    
    Args:
        timestamp: Unix timestamp
        
    Returns:
        Block number
    """
    if not MORALIS_API_KEY:
        logger.error("❌ MORALIS_API_KEY not set in environment")
        return None
    
    try:
        url = "https://deep-index.moralis.io/api/v2.2/dateToBlock"
        params = {
            "chain": "base",
            "date": str(timestamp)
        }
        headers = {
            "accept": "application/json",
            "X-API-Key": MORALIS_API_KEY
        }
        
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()
        data = response.json()
        
        block = data.get("block")
        logger.info(f"✅ Found block {block} for timestamp {timestamp}")
        return block
    except Exception as e:
        logger.error(f"❌ Failed to get block by timestamp: {e}")
        # Fallback method if Moralis fails
        return estimate_block_by_timestamp(timestamp)

def estimate_block_by_timestamp(timestamp):
    """
    Fallback method to estimate block number using binary search
    This is slower but doesn't require external API
    
    Args:
        timestamp: Unix timestamp
        
    Returns:
        Estimated block number
    """
    logger.info("Using fallback method to estimate block number...")
    w3 = get_web3()
    if not w3:
        return None
    
    try:
        # Get current block and timestamp
        current_block = w3.eth.block_number
        current_ts = w3.eth.get_block(current_block).timestamp
        
        # Base block averages - these may need to be adjusted for the chain
        avg_block_time = 2  # Base chain block time in seconds
        
        # Estimate blocks difference
        time_diff = current_ts - timestamp
        estimated_blocks = int(time_diff / avg_block_time)
        
        # First guess
        target_block = max(1, current_block - estimated_blocks)
        
        # Binary search to refine
        low_block = max(1, target_block - 10000)
        high_block = min(current_block, target_block + 10000)
        
        # Binary search with limited iterations
        for _ in range(20):  # Limit iterations to avoid infinite loop
            mid_block = (low_block + high_block) // 2
            mid_ts = w3.eth.get_block(mid_block).timestamp
            
            if mid_ts == timestamp:
                return mid_block
            
            if mid_ts < timestamp:
                low_block = mid_block + 1
            else:
                high_block = mid_block - 1
            
            # If we're within a close range, return the closest block
            if high_block - low_block <= 5:
                # Find the closest block within this small range
                closest_block = low_block
                closest_diff = abs(w3.eth.get_block(low_block).timestamp - timestamp)
                
                for b in range(low_block + 1, high_block + 1):
                    b_ts = w3.eth.get_block(b).timestamp
                    b_diff = abs(b_ts - timestamp)
                    
                    if b_diff < closest_diff:
                        closest_diff = b_diff
                        closest_block = b
                
                logger.info(f"✅ Found closest block {closest_block} for timestamp {timestamp}")
                return closest_block
        
        logger.info(f"✅ Estimated block {low_block} for timestamp {timestamp}")
        return low_block
        
    except Exception as e:
        logger.error(f"❌ Failed to estimate block by timestamp: {e}")
        return None

def get_epoch_from_block(block_number):
    """Get the epoch number at a specific block"""
    w3 = get_web3()
    if not w3:
        return 0
    
    try:
        minter = w3.eth.contract(
            address=w3.to_checksum_address(MINTER_ADDRESS),
            abi=MINTER_ABI
        )
        
        # Aero uses activePeriod for the current week
        period = minter.functions.activePeriod().call(block_identifier=block_number)
        logger.info(f"ℹ️ Period at block {block_number}: {period}")
        return period
    except Exception as e:
        logger.error(f"❌ Failed to get period at block {block_number}: {e}")
        return 0

def get_total_weight_at_block(block_number):
    """Get total weight (votes) from Voter contract at a specific block"""
    w3 = get_web3()
    if not w3:
        return Decimal('0')
    
    try:
        voter = w3.eth.contract(
            address=w3.to_checksum_address(VOTER_ADDRESS),
            abi=VOTER_ABI
        )
        
        # For Aero, we use totalWeight to get the total votes/weight
        total_weight = voter.functions.totalWeight().call(block_identifier=block_number)
        total_weight_decimal = from_wei(total_weight)
        logger.info(f"ℹ️ Total weight at block {block_number}: {total_weight_decimal}")
        return total_weight_decimal
    except Exception as e:
        logger.error(f"❌ Failed to get total weight at block {block_number}: {e}")
        return Decimal('0')

def get_pool_weight_at_block(pool_addr, block_number):
    """Get weight (votes) for a specific pool at a specific block"""
    w3 = get_web3()
    if not w3:
        return Decimal('0')
    
    try:
        voter = w3.eth.contract(
            address=w3.to_checksum_address(VOTER_ADDRESS),
            abi=VOTER_ABI
        )
        
        # For Aero, we use weights(pool) to get the votes/weight for a pool
        weight = voter.functions.weights(w3.to_checksum_address(pool_addr)).call(block_identifier=block_number)
        return from_wei(weight)
    except Exception as e:
        logger.error(f"❌ Failed to get pool weight for {pool_addr} at block {block_number}: {e}")
        return Decimal('0')

def get_weekly_emissions_at_block(block_number):
    """Get weekly emissions from Minter contract at a specific block"""
    w3 = get_web3()
    if not w3:
        return Decimal('0')
    
    try:
        minter = w3.eth.contract(
            address=w3.to_checksum_address(MINTER_ADDRESS),
            abi=MINTER_ABI
        )
        
        weekly_wei = minter.functions.weekly().call(block_identifier=block_number)
        weekly = from_wei(weekly_wei)
        logger.info(f"ℹ️ Weekly emissions at block {block_number}: {weekly} AERO")
        return weekly
    except Exception as e:
        logger.error(f"❌ Failed to get weekly emissions at block {block_number}: {e}")
        return Decimal('0')

def fetch_all_pools_at_block(block_number):
    """Fetch all pools from LpSugar contract at a specific block"""
    w3 = get_web3()
    if not w3:
        return []
    
    logger.info(f"🔍 Fetching all pools via LpSugar at block {block_number}...")
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
            batch = lp_sugar.functions.all(PAGE_SIZE, offset).call(block_identifier=block_number)
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
    
    logger.info(f"✅ Retrieved {len(formatted_pools)} pools at block {block_number}")
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
    
    logger.info(f"→ {len(votable)} votable pools after filtering")
    return votable

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

def fetch_relay_votes_at_block(w3, pools, block_number):
    """Fetch relay votes data at a specific block"""
    logger.info(f"🔍 Fetching relay votes data at block {block_number}...")
    
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
        relays_raw = relay_sugar.functions.all(w3.to_checksum_address(RELAY_ACCOUNT)).call(block_identifier=block_number)
        logger.info(f"→ Retrieved {len(relays_raw)} Relay entries at block {block_number}")
        
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
        
        logger.info(f"✅ Processed relay votes for {len(relay_totals)} pools at block {block_number}")
        return relay_totals
    
    except Exception as e:
        logger.error(f"❌ Error fetching relay votes at block {block_number}: {e}")
        return {}
    
    enriched_pools = []
    zero_addr = "0x0000000000000000000000000000000000000000"
    
    for pool in pools:
        # Ensure we have symbol
        symbol = pool.get("symbol", "") or ""
        if not symbol or symbol.lower().startswith("0x"):
            token0 = pool.get("token0", zero_addr)
            token1 = pool.get("token1", zero_addr)
            
            sym0 = get_token_symbol(w3, token0, block_number) or token0[:6]
            sym1 = get_token_symbol(w3, token1, block_number) or token1[:6]
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
        dec0 = get_token_decimals(w3, token0, block_number)
        dec1 = get_token_decimals(w3, token1, block_number)
        
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
    
    logger.info(f"Enriched {len(enriched_pools)} pools with TVL data at block {block_number}")
    logger.info(f"Top 3 pools by TVL: {[f'{p.get('symbol')} (${p.get('tvl_usd'):,.2f})' for p in enriched_pools[:3]]}")
    return enriched_pools

def calculate_lp_data_at_block(pools, block_number, investment_sizes=None):
    """
    Calculate LP APR data for all pools at different investment sizes at a specific block
    
    Args:
        pools: List of pool data
        block_number: Block number to fetch data from
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
    
    # Get total weight
    on_chain_total_weight = get_total_weight_at_block(block_number)
    
    # Get relay votes
    relay_votes = fetch_relay_votes_at_block(w3, pools, block_number)
    
    # Calculate total relay votes
    total_relay_votes = sum(relay_votes.values())
    logger.info(f"ℹ️ Total relay votes at block {block_number}: {total_relay_votes}")
    
    # Add relay votes to on-chain weight for grand total
    adjusted_total_weight = on_chain_total_weight + total_relay_votes
    logger.info(f"ℹ️ Adjusted total weight (including relays) at block {block_number}: {adjusted_total_weight}")
    
    # Get weekly emissions and price
    weekly_emissions = get_weekly_emissions_at_block(block_number)
    aero_price = get_aero_price()  # Use current AERO price
    weekly_emissions_usd = weekly_emissions * aero_price
    
    logger.info(f"ℹ️ Weekly emissions at block {block_number}: {weekly_emissions} AERO (${weekly_emissions_usd})")
    
    updated_pools = []
    for pool in pools:
        # Use pool address for voting weights, not gauge address
        pool_address = pool.get('pool')
        
        # Get on-chain weight for this pool
        weight = get_pool_weight_at_block(pool_address, block_number)
        
        # Get relay votes for this pool
        relay_weight = relay_votes.get(pool_address.lower(), Decimal('0'))
        
        # Add relay votes to regular votes for total weight
        total_pool_weight = weight + relay_weight
        
        # Calculate weight percentage based on combined weight and adjusted total
        weight_pct = (total_pool_weight / adjusted_total_weight * 100) if adjusted_total_weight > 0 else Decimal('0')
        
        # Calculate rewards based on combined weight allocation and adjusted total
        rewards = (total_pool_weight / adjusted_total_weight) * weekly_emissions_usd if adjusted_total_weight > 0 else Decimal('0')
        
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
            'total_pool_weight': float(total_pool_weight),
            'weight_pct': float(weight_pct),
            'weekly_rewards': float(rewards),
            'apr': float(base_apr),
            'apr_by_investment': {str(size): float(apr) for size, apr in apr_by_investment.items()}
        })
        
        updated_pools.append(updated_pool)
    
    # Sort by APR, descending
    updated_pools.sort(key=lambda x: x.get('apr', 0), reverse=True)
    
    return updated_pools

def fetch_token_prices(tokens):
    """Fetch token prices from CoinGecko"""
    logger.info(f"🔍 Fetching prices for {len(tokens)} tokens...")
    
    # Map addresses to CoinGecko IDs
    token_ids = fetch_coingecko_token_ids(tokens)
    
    # Fetch prices
    prices = {}
    
    if token_ids:
        try:
            # Split into batches of 100 due to CoinGecko API limits
            batches = [list(token_ids.values())[i:i + 100] for i in range(0, len(token_ids), 100)]
            
            for batch in batches:
                ids = ','.join(batch)
                params = {
                    'ids': ids,
                    'vs_currencies': 'usd'
                }
                
                response = requests.get(COINGECKO_SIMPLE_PRICE_URL, params=params)
                response.raise_for_status()
                price_data = response.json()
                
                # Map CoinGecko IDs back to token addresses
                for addr, cg_id in token_ids.items():
                    if cg_id in price_data and 'usd' in price_data[cg_id]:
                        prices[addr] = Decimal(str(price_data[cg_id]['usd']))
            
            logger.info(f"✅ Retrieved prices for {len(prices)} tokens")
        except Exception as e:
            logger.error(f"❌ Failed to fetch token prices: {e}")
    
    return prices

def enrich_pools_with_tvl(pools, block_number):
    """Enrich pools with TVL data"""
    logger.info(f"🔍 Enriching pools with TVL data at block {block_number}...")
    
    w3 = get_web3()
    if not w3:
        return pools
    
    # Collect all token addresses
    token_addresses = set()
    for pool in pools:
        token0 = pool.get("token0", "").lower()
        token1 = pool.get("token1", "").lower()
        if token0 and token0 != "0x0000000000000000000000000000000000000000":
            token_addresses.add(token0)
        if token1 and token1 != "0x0000000000000000000000000000000000000000":
            token_addresses.add(token1)
    
    # Fetch token prices
    token_prices = fetch_token_prices(token_addresses)
    
    enriched_pools = []
    zero_addr = "0x0000000000000000000000000000000000000000"
    
    for pool in pools:
        # Ensure we have symbol
        symbol = pool.get("symbol", "") or ""
        if not symbol or symbol.lower().startswith("0x"):
            token0 = pool.get("token0", zero_addr)
            token1 = pool.get("token1", zero_addr)
            
            sym0 = get_token_symbol(w3, token0, block_number) or token0[:6]
            sym1 = get_token_symbol(w3, token1, block_number) or token1[:6]
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
        dec0 = get_token_decimals(w3, token0, block_number)
        dec1 = get_token_decimals(w3, token1, block_number)
        
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
    
    logger.info(f"Enriched {len(enriched_pools)} pools with TVL data at block {block_number}")
    logger.info(f"Top 3 pools by TVL: {[f'{p.get('symbol')} (${p.get('tvl_usd'):,.2f})' for p in enriched_pools[:3]]}")
    return enriched_pools

def run_fetch_lp_data_previous_epoch():
    """Main function to fetch LP data for previous epoch"""
    logger.info("🔄 Starting previous epoch LP data fetch...")
    
    # Step 1: Get the timestamp for previous epoch (just before epoch change)
    timestamp = get_previous_epoch_timestamp()
    date_str = datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc).strftime('%Y%m%d')
    
    # Step 2: Get the block number for that timestamp
    block_number = get_block_by_timestamp(timestamp)
    if not block_number:
        logger.error("❌ Failed to get block number for timestamp")
        return
    
    # Step 3: Get the epoch number at that block
    epoch = get_epoch_from_block(block_number)
    
    logger.info(f"📊 Processing LP data for epoch {epoch} at block {block_number} (timestamp: {timestamp})")
    
    # Step 4: Fetch all pools from LpSugar at that block
    all_pools = fetch_all_pools_at_block(block_number)
    
    # Step 5: Filter for votable pools
    votable_pools = filter_votable_pools(all_pools)
    
    # Step 6: Enrich pools with TVL data
    enriched_pools = enrich_pools_with_tvl(votable_pools, block_number)
    
    # Step 7: Calculate LP data with votes and APR
    lp_data = calculate_lp_data_at_block(enriched_pools, block_number)
    
    # Step 8: Prepare dashboard data
    dashboard = {
        "timestamp": timestamp,
        "date": datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
        "block": block_number,
        "epoch": epoch,
        "pools": lp_data
    }
    
    # Step 9: Save dashboard data
    # Create directory if it doesn't exist
    os.makedirs("lp_dashboard_previous/aero", exist_ok=True)
    
    # Save with date in filename
    dated_path = f"lp_dashboard_previous/aero/lp_dashboard_{date_str}.json"
    save_json(dashboard, dated_path)
    
    # Also save as the default file
    default_path = "lp_dashboard_previous/aero/lp_dashboard.json"
    save_json(dashboard, default_path)
    
    logger.info(f"✅ Previous epoch LP data saved to {dated_path} and {default_path}")
    
    return dashboard

if __name__ == "__main__":
    run_fetch_lp_data_previous_epoch()