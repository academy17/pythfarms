
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
    """Get AERO token price from CoinGecko with retry mechanism"""
    import time
    import random
    
    max_retries = 5
    base_delay = 2  # Start with a 2-second delay
    
    for attempt in range(max_retries):
        try:
            # Add a small random delay before each request to avoid rate limiting
            if attempt > 0:
                # Exponential backoff with jitter
                delay = base_delay * (2 ** attempt) + random.uniform(0.1, 1.0)
                logger.info(f"Rate limited by CoinGecko. Retrying in {delay:.2f} seconds (attempt {attempt+1}/{max_retries})...")
                time.sleep(delay)
            
            params = {
                'ids': COINGECKO_AERO_ID,
                'vs_currencies': 'usd'
            }
            
            # Add a custom user agent to avoid being blocked
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36',
                'Accept': 'application/json'
            }
            
            response = requests.get(COINGECKO_SIMPLE_PRICE_URL, params=params, headers=headers, timeout=10)
            
            # Check for rate limiting response
            if response.status_code == 429:
                logger.warning(f"CoinGecko rate limit hit. Will retry...")
                continue
                
            response.raise_for_status()
            data = response.json()
            
            price = Decimal(str(data.get(COINGECKO_AERO_ID, {}).get('usd', 0)))
            logger.info(f"ℹ️ AERO price: ${price}")
            return price
            
        except requests.exceptions.RequestException as e:
            logger.warning(f"CoinGecko API request failed (attempt {attempt+1}/{max_retries}): {e}")
            # Only continue retrying for specific errors
            if "429" in str(e) or "timeout" in str(e).lower():
                continue
            else:
                logger.error(f"❌ Failed to get AERO price (non-retriable error): {e}")
                break
        except Exception as e:
            logger.error(f"❌ Failed to get AERO price: {e}")
            break
    
    # If we've exhausted all retries or hit a non-retriable error, use fallback price
    # You might want to use a hardcoded recent price as a last resort
    logger.warning("Using fallback AERO price after all retries failed")
    return Decimal('1.01')  # Approximate recent price as fallback

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
        
        # Add type_name based on pool type
        pool_type = pool.get("type", -1)
        if pool_type == 0:
            pool["type_name"] = "Stable"
        elif pool_type == 1:
            pool["type_name"] = "Volatile"
        else:
            pool["type_name"] = "Concentrated Liquidity"
        
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
    logger.info(f"Top 3 pools by TVL: {[f'{p.get('symbol')} (${p.get('tvl_usd'):,.2f})' for p in enriched_pools[:3]]}")
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
        
    # Get total weight
    on_chain_total_weight = get_total_weight()
    
    # Get relay votes
    relay_votes = fetch_relay_votes(w3, pools)
    
    # Calculate total relay votes
    total_relay_votes = sum(relay_votes.values())
    logger.info(f"ℹ️ Total relay votes: {total_relay_votes}")
    
    # Add relay votes to on-chain weight for grand total
    adjusted_total_weight = on_chain_total_weight + total_relay_votes
    logger.info(f"ℹ️ Adjusted total weight (including relays): {adjusted_total_weight}")
    
    # Get weekly emissions and price
    weekly_emissions = get_weekly_emissions()
    aero_price = get_aero_price()
    weekly_emissions_usd = weekly_emissions * aero_price
    
    logger.info(f"ℹ️ Weekly emissions: {weekly_emissions} AERO (${weekly_emissions_usd})")
    
    # Fetch our LP positions to integrate with dashboard
    logger.info("🔍 Fetching our LP positions to integrate with dashboard...")
    
    # Import the functions here to avoid circular imports
    from .fetch_our_lp_data import fetch_our_positions, calculate_positions_value
    
    # Fetch LP positions from all addresses defined in the environment variables
    our_positions = fetch_our_positions(w3)
    
    # Instead of calling calculate_positions_value which would make another API call to CoinGecko,
    # we'll calculate the values ourselves using the token prices we already have
    enriched_positions = []
    
    # Create a mapping of token addresses to prices from our existing data
    all_token_prices = {}
    for pool in pools:
        token0 = pool.get('token0', '').lower()
        token1 = pool.get('token1', '').lower()
        price0 = pool.get('token0_price', 0)
        price1 = pool.get('token1_price', 0)
        
        if token0 and price0:
            all_token_prices[token0] = Decimal(str(price0))
        if token1 and price1:
            all_token_prices[token1] = Decimal(str(price1))
    
    logger.info(f"Reusing {len(all_token_prices)} token prices from pools data")
    
    # Now calculate values using our existing price data
    for pos in our_positions:
        try:
            # Get pool from the position
            lp_contract = w3.eth.contract(
                address=w3.to_checksum_address(pos['lp']),
                abi=[{
                    "inputs": [],
                    "name": "token0",
                    "outputs": [{"type": "address", "name": ""}],
                    "stateMutability": "view",
                    "type": "function"
                }, {
                    "inputs": [],
                    "name": "token1",
                    "outputs": [{"type": "address", "name": ""}],
                    "stateMutability": "view",
                    "type": "function"
                }]
            )
            
            token0 = lp_contract.functions.token0().call().lower()
            token1 = lp_contract.functions.token1().call().lower()
            
            # Get symbols
            symbol0 = get_token_symbol(w3, token0) or token0[:6]
            symbol1 = get_token_symbol(w3, token1) or token1[:6]
            pool_symbol = f"{symbol0}/{symbol1}"
            
            # Get token decimals
            dec0 = get_token_decimals(w3, token0)
            dec1 = get_token_decimals(w3, token1)
            
            # Calculate amounts (staked + unstaked)
            amount0 = Decimal(str(pos['amount0'])) + Decimal(str(pos['staked0']))
            amount1 = Decimal(str(pos['amount1'])) + Decimal(str(pos['staked1']))
            
            # Convert to human readable
            amount0_human = amount0 / (Decimal(10) ** dec0)
            amount1_human = amount1 / (Decimal(10) ** dec1)
            
            # Get prices from our existing price data
            price0 = all_token_prices.get(token0, Decimal(0))
            price1 = all_token_prices.get(token1, Decimal(0))
            
            # Calculate total value
            value0_usd = amount0_human * price0
            value1_usd = amount1_human * price1
            total_value_usd = value0_usd + value1_usd
            
            enriched_pos = {
                'pool': pos['lp'],
                'symbol': pool_symbol,
                'amount0_human': float(amount0_human),
                'amount1_human': float(amount1_human),
                'token0_symbol': symbol0,
                'token1_symbol': symbol1,
                'token0_price': float(price0),
                'token1_price': float(price1),
                'value0_usd': float(value0_usd),
                'value1_usd': float(value1_usd),
                'total_value_usd': float(total_value_usd)
            }
            
            enriched_positions.append(enriched_pos)
            
        except Exception as e:
            logger.error(f"Error processing position for pool {pos['lp']}: {e}")
    
    # Create lookup of pools where we have LP positions
    our_lp_pools = {}
    for pos in enriched_positions:
        pool_addr = pos['pool'].lower()
        
        # If we already have this pool, sum up the values
        if pool_addr in our_lp_pools:
            our_lp_pools[pool_addr]['amount0_human'] += pos.get('amount0_human', 0)
            our_lp_pools[pool_addr]['amount1_human'] += pos.get('amount1_human', 0)
            our_lp_pools[pool_addr]['value0_usd'] += pos.get('value0_usd', 0)
            our_lp_pools[pool_addr]['value1_usd'] += pos.get('value1_usd', 0)
            our_lp_pools[pool_addr]['total_value_usd'] += pos.get('total_value_usd', 0)
        else:
            # Otherwise, create a new entry
            our_lp_pools[pool_addr] = {
                'amount0_human': pos.get('amount0_human', 0),
                'amount1_human': pos.get('amount1_human', 0),
                'value0_usd': pos.get('value0_usd', 0),
                'value1_usd': pos.get('value1_usd', 0),
                'total_value_usd': pos.get('total_value_usd', 0)
            }
            
        logger.info(f"Our LP in pool {pos['symbol']} ({pool_addr}): ${pos.get('total_value_usd', 0):.2f}")
    
    logger.info(f"✅ Found {len(our_lp_pools)} pools where we have LP positions")
    
    # Log the summed values for each pool
    for pool_addr, pool_data in our_lp_pools.items():
        logger.info(f"Summed LP in pool {pool_addr}: ${pool_data.get('total_value_usd', 0):.2f}")
    
    logger.info("Optimizing pool processing...")
    
    # First, fetch weights only for pools where we have LP positions
    # This reduces RPC calls dramatically
    our_lp_pool_addresses = list(our_lp_pools.keys())
    
    # Create a mapping of pool addresses to weights
    # We only fetch weights for pools where we have LP positions
    pool_weights = {}
    for addr in our_lp_pool_addresses:
        weight = get_pool_weight(addr)
        pool_weights[addr] = weight
        logger.info(f"Fetched weight for our LP pool {addr}: {weight}")
    
    # Batch process the rest of the pools
    updated_pools = []
    processed_count = 0
    
    for pool in pools:
        pool_address = pool.get('pool')
        pool_address_lower = pool_address.lower()
        
        # Check if we have LP position in this pool
        our_lp_data = our_lp_pools.get(pool_address_lower, {})
        has_our_lp = pool_address_lower in our_lp_pools
        
        # Get weight - only fetch from chain for our LP pools, default to 0 for others
        weight = pool_weights.get(pool_address_lower, Decimal('0'))
        
        # Get relay votes for this pool
        relay_weight = relay_votes.get(pool_address_lower, Decimal('0'))
        
        # Store relay weight for reference but don't use it in calculations currently
        total_pool_weight = weight  # Remove relay_weight from this calculation
        
        # Calculate weight percentage based on on-chain weight only
        weight_pct = (weight / on_chain_total_weight * 100) if on_chain_total_weight > 0 else Decimal('0')
        
        # Calculate rewards based on on-chain weight allocation only
        rewards = (weight / on_chain_total_weight) * weekly_emissions_usd if on_chain_total_weight > 0 else Decimal('0')
        
        # Calculate base APR
        tvl_usd = Decimal(str(pool.get('tvl_usd', 0)))
        
        # Calculate APR - prevent division by zero
        if tvl_usd <= Decimal('0.01'):  # Very small TVL
            base_apr = Decimal('0')
        else:
            base_apr = (rewards * 52 / tvl_usd * 100)
        
        # Calculate APR at different investment sizes - only if we have LP or it's a top pool
        apr_by_investment = {}
        if has_our_lp or processed_count < 50:  # Only calculate for our LP pools or top 50 pools
            for size in investment_sizes:
                apr_by_investment[str(size)] = calculate_apr_at_investment_size(pool, size, rewards)
        
        if has_our_lp:
            logger.info(f"Processing pool with our LP: {pool.get('symbol')} ({pool_address})")
            logger.info(f"  Value: ${our_lp_data.get('total_value_usd', 0):.2f}")
        
        # Update pool with calculated data
        updated_pool = pool.copy()
        updated_pool.update({
            'weight': float(weight),
            'relay_votes': float(relay_weight),
            'total_pool_weight': float(total_pool_weight),
            'weight_pct': float(weight_pct),
            'weekly_rewards_usd': float(rewards),
            'apr': float(base_apr),
            'apr_by_investment': {str(size): float(apr) for size, apr in apr_by_investment.items()},
            'has_our_lp': has_our_lp,
            'our_lp_data': our_lp_data if has_our_lp else None
        })
        
        updated_pools.append(updated_pool)
        processed_count += 1
        
        # Log progress periodically
        if processed_count % 50 == 0:
            logger.info(f"Processed {processed_count}/{len(pools)} pools...")
    
    # Sort by APR, descending
    updated_pools.sort(key=lambda x: x.get('apr', 0), reverse=True)
    
    # Filter the fields to keep only what's needed
    cleaned_pools = []
    for pool in updated_pools:
        # Keep only the required fields for each pool
        cleaned_pool = {
            'lp': pool.get('lp'),
            'symbol': pool.get('symbol'),
            'decimals': pool.get('decimals'),
            'liquidity': pool.get('liquidity'),
            'type': pool.get('type'),
            'type_name': pool.get('type_name'),
            'token0': pool.get('token0'),
            'token1': pool.get('token1'),
            'gauge': pool.get('gauge'),
            'gauge_alive': pool.get('gauge_alive'),
            'emissions': pool.get('emissions'),
            'pool_fee': pool.get('pool_fee'),
            'token0_fees': pool.get('token0_fees'),
            'token1_fees': pool.get('token1_fees'),
            'tvl_usd': pool.get('tvl_usd'),
            'token0_price': pool.get('token0_price'),
            'token1_price': pool.get('token1_price'),
            'relay_votes': pool.get('relay_votes'),
            'total_pool_weight': pool.get('total_pool_weight'),
            'weight_pct': pool.get('weight_pct'),
            'weekly_rewards_usd': pool.get('weekly_rewards_usd'),
            'apr': pool.get('apr'),
            'apr_by_investment': pool.get('apr_by_investment'),
            'has_our_lp': pool.get('has_our_lp', False),
            'our_lp_data': pool.get('our_lp_data')
        }
        cleaned_pools.append(cleaned_pool)
    
    # Sort by TVL, descending
    cleaned_pools.sort(key=lambda x: x.get('tvl_usd', 0), reverse=True)
    
    return cleaned_pools

def save_lp_dashboard(lp_data, investment_sizes=None):
    """
    Save LP data to dashboard files
    
    Args:
        lp_data: Processed LP data with APRs
        investment_sizes: List of investment amounts used for APR calculation
    """
    if investment_sizes is None:
        investment_sizes = DEFAULT_INVESTMENT_SIZES
    
    w3 = get_web3()
    if not w3:
        logger.error("❌ Failed to connect to RPC")
        return None
    
    current_period = get_current_epoch()
    date_str = datetime.datetime.now().strftime("%Y%m%d")
    
    # Get weights for context
    on_chain_weight = get_total_weight()
    relay_votes = fetch_relay_votes(w3, lp_data)
    total_relay_votes = sum(relay_votes.values())
    adjusted_total_weight = on_chain_weight + total_relay_votes
    
    # Calculate our LP position totals
    pools_with_our_lp = [p for p in lp_data if p.get('has_our_lp', False)]
    our_lp_pools_count = len(pools_with_our_lp)
    our_lp_total_value = sum(p.get('our_lp_data', {}).get('total_value_usd', 0) 
                              for p in lp_data if p.get('has_our_lp', False))
    
    # Get weekly emissions data directly from source
    weekly_emissions = get_weekly_emissions()
    aero_price = get_aero_price()
    total_weekly_emissions_usd = weekly_emissions * aero_price
    
    logger.info(f"Final Summary: Found {our_lp_pools_count} pools with our LP positions out of {len(lp_data)} total pools")
    logger.info(f"Total value of our LP positions: ${our_lp_total_value:.2f}")
    logger.info(f"Total weekly emissions: {weekly_emissions} AERO (${total_weekly_emissions_usd})")
    
    if our_lp_pools_count > 0:
        for p in pools_with_our_lp:
            logger.info(f"LP in {p.get('symbol')}: ${p.get('our_lp_data', {}).get('total_value_usd', 0):.2f}")
    
    # Create dashboard data
    dashboard = {
        'period': current_period,
        'date': date_str,
        'investment_sizes': [float(size) for size in investment_sizes],
        'on_chain_total_weight': float(on_chain_weight),
        'total_relay_votes': float(total_relay_votes),
        'adjusted_total_weight': float(adjusted_total_weight),
        'total_weekly_emissions': float(weekly_emissions),
        'aero_price': float(aero_price),
        'total_weekly_emissions_usd': float(total_weekly_emissions_usd),
        'note': "APR calculations now use on-chain weight only, relay votes are implied and carried over from last epoch",
        'our_lp_summary': {
            'pools_count': our_lp_pools_count,
            'total_value_usd': float(our_lp_total_value)
        },
        'pools': lp_data
    }    # Save to files
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
    
    logger.info("Generating dashboard display...")
    
    # First, get pools with our LP positions
    our_lp_pools = [p for p in pools if p.get('has_our_lp', False)]
    
    # Then get top pools by TVL for the rest
    pools_without_our_lp = [p for p in pools if not p.get('has_our_lp', False)]
    sorted_by_tvl = sorted(pools_without_our_lp, key=lambda x: x.get('tvl_usd', 0), reverse=True)
    
    # Combine our LP pools with top TVL pools
    display_pools = our_lp_pools + sorted_by_tvl[:max(0, top_n - len(our_lp_pools))]
    
    # Format investment sizes for display
    investment_str = [f"${size/1000}k" for size in investment_sizes]
    
    # Print header
    print("\n================ AERO LP DASHBOARD ================")
    print(f"Date: {datetime.datetime.now().strftime('%Y-%m-%d')}")
    
    # Use cached total weights for display (avoid additional RPC calls)
    on_chain_weight = sum(Decimal(str(p.get('weight', 0))) for p in pools)
    total_relay_votes = sum(Decimal(str(p.get('relay_votes', 0))) for p in pools)
    adjusted_total_weight = on_chain_weight + total_relay_votes
    
    print(f"On-chain weight: {on_chain_weight:,.2f}")
    print(f"Total relay votes: {total_relay_votes:,.2f} (not used in APR calculation)")
    print(f"Adjusted total weight: {adjusted_total_weight:,.2f}")
    print(f"NOTE: APR calculations now use on-chain weight only, ignoring relay votes")
    
    print(f"Showing {len(our_lp_pools)} pools with our LP positions + {min(top_n - len(our_lp_pools), len(sorted_by_tvl))} top pools by TVL")
    print("--------------------------------------------------")
    
    # Print column headers
    header = f"{'Pool':20} {'TVL':>12} {'Weight':>10} {'APR':>8} {'Our LP':>8}"
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
        weight_val = pool.get('total_pool_weight', 0)
        if weight_val < 1000:
            weight = f"{weight_val:.2f}".rjust(10)
        elif weight_val < 1000000:  # Less than 1M
            weight = f"{weight_val/1000:.2f}K".rjust(10)
        else:  # 1M or more
            weight = f"{weight_val/1000000:.2f}M".rjust(10)
            
        apr = f"{pool.get('apr', 0):.2f}%".rjust(8)
        
        # Add our LP indicator
        has_our_lp = pool.get('has_our_lp', False)
        our_lp_indicator = "✓".rjust(8) if has_our_lp else "".rjust(8)
        
        line = f"{symbol} {tvl} {weight} {apr} {our_lp_indicator}"
        
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
    
    # Step 3: Enrich pools with symbols and TVL
    enriched_pools = enrich_pools(w3, votable_pools)
    
    # Step 4: Calculate LP APR data for all votable pools
    # We calculate APR for all pools even though we might only display the top N
    lp_data = calculate_lp_data(enriched_pools, investment_sizes)
    
    # Step 5: Display dashboard - only show top pools by APR
    if display:
        display_lp_dashboard(lp_data, investment_sizes, top_n)
    
    # Step 6: Save dashboard - save ALL pools to ensure complete data for other tools
    if save:
        save_lp_dashboard(lp_data, investment_sizes)
    
    logger.info("✅ LP Dashboard generation complete!")
    return lp_data

if __name__ == "__main__":
    run_fetch_lp_data()
