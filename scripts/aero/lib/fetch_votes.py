#!/usr/bin/env python3
# filepath: d:\Pyth\pythfarms\scripts\aero\lib\fetch_votes.py
import os
import json
import requests
import logging
import datetime
from decimal import Decimal
from web3 import Web3
from web3.exceptions import ContractLogicError
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()

# Constants
RPC_URL = os.getenv("RPC_URL")
LP_SUGAR_ADDRESS = os.getenv("LP_SUGAR_ADDRESS")
REWARDS_SUGAR_ADDR = os.getenv("REWARDS_SUGAR_ADDRESS")
VOTER_ADDRESS = os.getenv("VOTER_ADDRESS")
VE_ADDRESS = os.getenv("VE_ADDRESS")
NFT_ID = int(os.getenv("NFT_ID", "0"))
PAGE_SIZE = int(os.getenv("PAGE_SIZE", 200))
DASHBOARD_PATH = "input_data/aero/votes_dashboard.json"

# CoinGecko URLs
COINGECKO_SIMPLE_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"
COINGECKO_COINS_LIST_URL = "https://api.coingecko.com/api/v3/coins/list?include_platform=true"

# ABIs
def load_abi(name):
    with open(f"abi/aero/{name}.json") as f:
        return json.load(f)

LP_SUGAR_ABI = load_abi("LpSugar")
REWARDS_SUGAR_ABI = load_abi("RewardsSugar")
VOTER_ABI = load_abi("Voter")
VE_ABI = load_abi("Ve")

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

def get_web3():
    """Initialize and return a Web3 instance"""
    return Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 60}))

def save_json(data, path):
    """Save data to a JSON file"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)
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

def fetch_all_pools(w3):
    """Fetch all pools from LpSugar contract"""
    logger.info("Fetching all pools via LpSugar...")
    lp_sugar = w3.eth.contract(
        address=w3.to_checksum_address(LP_SUGAR_ADDRESS),
        abi=LP_SUGAR_ABI
    )
    
    # Extract field names from ABI
    fn_abi = next((item for item in lp_sugar.abi if item.get("name") == "all" and item.get("type") == "function"), None)
    if not fn_abi:
        logger.error("Could not find 'all' function in LpSugar ABI")
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
    
    logger.info(f"Retrieved {len(formatted_pools)} pools")
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
    
    # Sort by liquidity
    votable.sort(key=lambda x: int(x["liquidity"]), reverse=True)
    
    logger.info(f"Found {len(votable)} votable pools out of {len(pools)}")
    return votable

def enrich_pools(w3, pools):
    """Add token symbols to pools"""
    logger.info("Enriching pools with token symbols...")
    enriched_pools = []
    zero_addr = "0x0000000000000000000000000000000000000000"
    
    for pool in pools:
        symbol = pool.get("symbol", "") or ""
        if not symbol or symbol.lower().startswith("0x"):
            token0 = pool.get("token0", zero_addr)
            token1 = pool.get("token1", zero_addr)
            
            sym0 = get_token_symbol(w3, token0) or token0[:6]
            sym1 = get_token_symbol(w3, token1) or token1[:6]
            symbol = f"{sym0}/{sym1}"
        
        pool["symbol"] = symbol
        enriched_pools.append(pool)
    
    logger.info(f"Enriched {len(enriched_pools)} pools")
    return enriched_pools

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

def current_epoch_start_ts():
    """Returns UNIX timestamp for the most recent Thursday 00:00 UTC"""
    now = datetime.datetime.utcnow()
    days_back = (now.weekday() - 3) % 7  # Thursday is 3
    thursday = now - datetime.timedelta(days=days_back)
    th_start = datetime.datetime(
        year=thursday.year, month=thursday.month, day=thursday.day,
        hour=0, minute=0, second=0, microsecond=0,
        tzinfo=datetime.timezone.utc
    )
    return int(th_start.timestamp())

def fetch_fees_and_bribes(w3, pool_info, contract_prices):
    """Fetch fees and bribes data from RewardsSugar"""
    logger.info("Fetching live fees and bribes data...")
    rewards_sugar = w3.eth.contract(
        address=w3.to_checksum_address(REWARDS_SUGAR_ADDR),
        abi=REWARDS_SUGAR_ABI
    )
    
    epoch_start = current_epoch_start_ts()
    logger.info(f"Current epoch start: {datetime.datetime.utcfromtimestamp(epoch_start).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    
    results = []
    ZERO = "0x0000000000000000000000000000000000000000"
    
    for pool_addr, info in pool_info.items():
        try:
            ep_arr = rewards_sugar.functions.epochsByAddress(1, 0, w3.to_checksum_address(pool_addr)).call()
        except ContractLogicError:
            continue
        if not ep_arr:
            continue
        
        ep = ep_arr[0]
        ts = ep[0]
        bribes_arr = ep[4]
        fees_arr = ep[5]
        
        # Process fees
        fee0_amt = 0
        fee1_amt = 0
        fees_usd = Decimal(0)
        
        if ts == epoch_start:
            t0 = info["token0"]
            t1 = info["token1"]
            
            for tok, amt in fees_arr:
                tok_l = tok.lower()
                if tok_l == t0:
                    fee0_amt = int(amt)
                elif tok_l == t1:
                    fee1_amt = int(amt)
            
            # Convert to USD
            if fee0_amt > 0:
                price0 = contract_prices.get(t0)
                if price0 is not None:
                    dec0 = get_token_decimals(w3, t0)
                    amt0 = Decimal(fee0_amt) / (Decimal(10) ** dec0)
                    fees_usd += (amt0 * price0)
            
            if fee1_amt > 0:
                price1 = contract_prices.get(t1)
                if price1 is not None:
                    dec1 = get_token_decimals(w3, t1)
                    amt1 = Decimal(fee1_amt) / (Decimal(10) ** dec1)
                    fees_usd += (amt1 * price1)
        
        # Process bribes
        bribes_usd = Decimal(0)
        bribe_list = []
        if ts == epoch_start:
            for tok, amt in bribes_arr:
                tok_l = tok.lower()
                raw_amt = int(amt)
                if raw_amt == 0 or tok_l == ZERO:
                    continue
                
                sym_b = get_token_symbol(w3, tok_l) or tok_l[:6]
                dec_b = get_token_decimals(w3, tok_l)
                human_amt = Decimal(raw_amt) / (Decimal(10) ** dec_b)
                
                price_b = contract_prices.get(tok_l)
                if price_b is not None:
                    amt_usd = human_amt * price_b
                    bribes_usd += amt_usd
                else:
                    amt_usd = Decimal(0)
                
                bribe_list.append({
                    "token": tok_l,
                    "symbol": sym_b,
                    "amount": raw_amt,
                    "amount_token": float(human_amt),
                    "amount_usd": float(amt_usd)
                })
        
        # Calculate TVL using reserves with proper decimals
        reserve0 = Decimal(str(info.get("reserve0", 0)))
        reserve1 = Decimal(str(info.get("reserve1", 0)))
        token0_price = contract_prices.get(info["token0"], Decimal(0))
        token1_price = contract_prices.get(info["token1"], Decimal(0))
        
        # Get token decimals
        dec0 = get_token_decimals(w3, info["token0"])
        dec1 = get_token_decimals(w3, info["token1"])
        
        # Convert reserves to human readable amounts
        reserve0_human = reserve0 / (Decimal(10) ** dec0)
        reserve1_human = reserve1 / (Decimal(10) ** dec1)
        
        # Calculate TVL
        tvl = (reserve0_human * token0_price) + (reserve1_human * token1_price)

        total_usd = fees_usd + bribes_usd
        
        # Determine pool type name based on type code
        type_name = "Concentrated Liquidity"  # default for unknown types
        if info["type"] == 0:
            type_name = "Stable"
        elif info["type"] == 1:
            type_name = "Volatile"
        
        results.append({
            "pool": pool_addr,
            "symbol": info["symbol"],
            "type": info["type"],
            "type_name": type_name,
            "fees_usd": float(fees_usd),
            "bribes_usd": float(bribes_usd),
            "bribes": bribe_list,
            "total_bribes_fees_usd": float(total_usd),
            "total_usd": float(total_usd),  # Adding for optimizer compatibility
            "token0_price": float(token0_price),
            "token1_price": float(token1_price),
            "reserve0": float(reserve0),
            "reserve1": float(reserve1),
            "tvl": float(tvl)
        })
    
    results.sort(key=lambda x: x["total_bribes_fees_usd"], reverse=True)
    
    logger.info(f"Processed fees and bribes for {len(results)} pools")
    return results

def fetch_relay_votes(w3, enriched_pools):
    """Fetch relay votes data"""
    logger.info("Fetching relay votes data...")
    
    relay_account = os.getenv("RELAY_ACCOUNT")
    relay_sugar_address = os.getenv("RELAY_SUGAR_ADDRESS")
    
    if not relay_account or not relay_sugar_address:
        logger.warning("Missing RELAY_ACCOUNT or RELAY_SUGAR_ADDRESS, skipping relay votes fetch")
        return []
    
    # Create a mapping of pool addresses to symbols for quick lookup
    pool_symbols = {p["lp"].lower(): p.get("symbol", "") for p in enriched_pools}
    
    try:
        # Load the RelaySugar ABI and create contract instance
        relay_sugar_abi = load_abi("RelaySugar")
        relay_sugar = w3.eth.contract(
            address=w3.to_checksum_address(relay_sugar_address),
            abi=relay_sugar_abi
        )
        
        # Fetch all relays for the account
        relays_raw = relay_sugar.functions.all(w3.to_checksum_address(relay_account)).call()
        logger.info(f"Retrieved {len(relays_raw)} Relay entries")
        
        parsed_relays = []
        for raw in relays_raw:
            # Parse relay struct
            decimals_raw = raw[1]
            voting_amount_raw = raw[3]
            votes_arr = raw[6] if isinstance(raw[6], list) else []
            relay_address = raw[11]
            raw_name = raw[14]
            name = raw_name if isinstance(raw_name, str) else ""
            
            voting_amount_hr = Decimal(voting_amount_raw) / (Decimal(10) ** int(decimals_raw))
            
            # Compute vote percentages
            vote_entries = []
            if voting_amount_hr > 0:
                for (pool_addr, weight_raw) in votes_arr:
                    pool_l = pool_addr.lower()
                    weight_hr = Decimal(weight_raw) / (Decimal(10) ** 18)
                    percent = (weight_hr / voting_amount_hr) * Decimal(100)
                    vote_entries.append({
                        "pool": pool_l,
                        "symbol": pool_symbols.get(pool_l, ""),
                        "weight": float(weight_hr),
                        "weight_pct": float(percent)
                    })
            
            # Format voting amount for human display
            voting_amount_str = f"{voting_amount_hr:,.6f}".rstrip("0").rstrip(".")
            
            parsed_relays.append({
                "relay": relay_address.lower(),
                "name": name,
                "voting_amount": voting_amount_str,
                "voting_amount_raw": float(voting_amount_hr),
                "votes": vote_entries
            })
        
        # Sort by voting amount
        parsed_relays.sort(key=lambda x: Decimal(str(x["voting_amount_raw"])), reverse=True)
        
        logger.info(f"Processed {len(parsed_relays)} relays")
        return parsed_relays
    
    except Exception as e:
        logger.error(f"Error fetching relay votes: {e}")
        return []

def build_relay_totals(relays):
    """Sum relay weights per pool"""
    out = {}
    for r in relays:
        for v in r.get("votes", []):
            addr = v["pool"].lower()
            whr = Decimal(str(v.get("weight_hr", 0)))
            out[addr] = out.get(addr, Decimal(0)) + whr
    return out

def create_votes_dashboard(w3, pools, relays=None):
    """Create final votes dashboard with on-chain weights"""
    logger.info("Creating votes dashboard...")
    voter = w3.eth.contract(
        address=w3.to_checksum_address(VOTER_ADDRESS),
        abi=VOTER_ABI
    )
    
    ve = w3.eth.contract(
        address=w3.to_checksum_address(VE_ADDRESS),
        abi=VE_ABI
    )
    
    # Get global weights
    total_weight = 0
    try:
        raw = voter.functions.totalWeight().call()
        total_weight = Decimal(raw) / Decimal(10**18)
    except Exception as e:
        logger.error(f"Error fetching total weight: {e}")
    
    # Get our NFT balance
    our_nft_weight = 0
    try:
        raw = ve.functions.balanceOfNFT(NFT_ID).call()
        our_nft_weight = Decimal(raw) / Decimal(10**18)
    except Exception as e:
        logger.error(f"Error fetching NFT balance: {e}")
    
    logger.info(f"Total voting weight: {total_weight}")
    logger.info(f"Our NFT weight: {our_nft_weight}")
    
    # Calculate relay totals
    relay_totals = {}
    if relays:
        relay_totals = build_relay_totals(relays)
        logger.info(f"Incorporated relay votes for {len(relay_totals)} pools")
    
    # Add weights to pools
    augmented_pools = []
    pool_summed_weights = Decimal(0)
    timestamp = current_epoch_start_ts()

    # Add timestamp information to dashboard
    current_epoch_time = datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc)
    
    for entry in pools:
        pool_addr = entry["pool"].lower()
        
        # Get pool weight
        weight_hr = 0
        try:
            raw = voter.functions.weights(w3.to_checksum_address(pool_addr)).call()
            weight_hr = Decimal(raw) / Decimal(10**18)
            # Sum the weights for all pools
            pool_summed_weights += weight_hr
        except Exception:
            pass
        
        # Get our votes for this pool
        our_votes_hr = 0
        try:
            raw = ve.functions.votes(NFT_ID, w3.to_checksum_address(pool_addr)).call()
            our_votes_hr = Decimal(raw) / Decimal(10**18)
        except Exception:
            pass
        
        # Get relay votes for this pool
        relay_votes_hr = relay_totals.get(pool_addr, Decimal(0))
        
        # Calculate weight percentages
        weight_pct = (weight_hr / total_weight * 100) if total_weight > 0 else 0

        # Calculate what percentage our vote would be
        our_vote_impact = 0
        if total_weight > 0 and our_nft_weight > 0:
            our_vote_impact = (our_nft_weight / total_weight) * 100

        e = entry.copy()
        e["on_chain_weight"] = float(weight_hr)
        e["on_chain_weight_pct"] = float(weight_pct)
        e["relay_weight"] = float(relay_votes_hr)
        e["total_weight"] = float(weight_hr + relay_votes_hr)
        e["our_votes"] = float(our_votes_hr)
        e["our_vote_impact"] = float(our_vote_impact)
        
        augmented_pools.append(e)
    
    logger.info(f"Sum of all pool weights: {pool_summed_weights}")
    
    # Create dashboard
    dashboard = {
        "timestamp": timestamp,
        "date": current_epoch_time.strftime('%Y-%m-%d %H:%M:%S'),
        "total_weight": float(total_weight),
        "our_voting_power": float(our_nft_weight),
        "pool_summed_weights": float(pool_summed_weights),
        "pools": augmented_pools
    }
    
    # Add relay information if available
    if relays:
        dashboard["relays"] = relays
    
    # Sort by total_bribes_fees_usd
    dashboard["pools"].sort(key=lambda x: x["total_bribes_fees_usd"], reverse=True)
    
    logger.info(f"Created dashboard with {len(augmented_pools)} pools")
    save_json(dashboard, DASHBOARD_PATH)
    return dashboard

def run_fetch(is_historical=False):
    """Main entry point for fetching votes data"""
    logger.info("Starting vote data fetch process")
    
    if not RPC_URL or not LP_SUGAR_ADDRESS:
        logger.error("Missing required environment variables")
        return
    
    w3 = get_web3()
    
    # Step 1: Fetch all pools
    all_pools = fetch_all_pools(w3)
    
    # Step 2: Filter votable pools
    votable_pools = filter_votable_pools(all_pools)
    
    # Step 3: Enrich pools with symbols
    enriched_pools = enrich_pools(w3, votable_pools)
    
    # Step 4: Get token list and fetch CoinGecko IDs
    tokens = set()
    for p in enriched_pools:
        t0 = p.get("token0", "").lower()
        t1 = p.get("token1", "").lower()
        if w3.is_address(t0):
            tokens.add(w3.to_checksum_address(t0).lower())
        if w3.is_address(t1):
            tokens.add(w3.to_checksum_address(t1).lower())
    
    token_to_id = fetch_coingecko_token_ids(tokens)
    
    # Step 5: Fetch prices and calculate fees/bribes
    contract_prices = fetch_prices_from_coingecko(token_to_id)
    
    # Prepare pool info mapping
    pool_info = {
        p["lp"].lower(): {
            "symbol": p.get("symbol", ""),
            "token0": p["token0"].lower(),
            "token1": p["token1"].lower(),
            "type": p.get("type"),
            "reserve0": p.get("reserve0", 0),
            "reserve1": p.get("reserve1", 0)
        }
        for p in enriched_pools
    }
    
    # Step 6: Fetch fees and bribes
    pools_with_fees = fetch_fees_and_bribes(w3, pool_info, contract_prices)
    
    # Step 7: Fetch relay votes
    relay_data = fetch_relay_votes(w3, enriched_pools)
    
    # Step 8: Create votes dashboard
    dashboard = create_votes_dashboard(w3, pools_with_fees, relay_data)
    
    logger.info("Vote data fetch completed successfully")
    return dashboard