#!/usr/bin/env python3
# filepath: d:\Pyth\pythfarms\scripts\aero\fetch_votes_previous_epoch.py

import os
import json
import time
import requests
import logging
import datetime
from decimal import Decimal
from web3 import Web3
from web3.exceptions import ContractLogicError
from dotenv import load_dotenv
from lib.fetch_votes import (
    fetch_coingecko_token_ids, fetch_prices_from_coingecko,
    get_token_symbol, get_token_decimals, save_json, get_web3
)

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
MORALIS_API_KEY = os.getenv("MORALIS_API_KEY")

# Relay support
RELAY_ACCOUNT = os.getenv("RELAY_ACCOUNT")
RELAY_SUGAR_ADDRESS = os.getenv("RELAY_SUGAR_ADDRESS")

# Directory for historical data
PREVIOUS_VOTES_DIR = "previous_votes/aero"

# ABIs
def load_abi(name):
    """Load ABI from file"""
    with open(f"abi/aero/{name}.json") as f:
        return json.load(f)

LP_SUGAR_ABI = load_abi("LpSugar")
REWARDS_SUGAR_ABI = load_abi("RewardsSugar")
VOTER_ABI = load_abi("Voter")
VE_ABI = load_abi("Ve")
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
        return None

def estimate_epoch_from_block(w3, block_number):
    """
    Estimate the epoch number at a given block
    
    Args:
        w3: Web3 instance
        block_number: Block number
    
    Returns:
        Epoch number
    """
    try:
        from lib.fetch_lp_data_previous_epoch import get_epoch_from_block
        return get_epoch_from_block(block_number)
    except (ImportError, AttributeError):
        # Fallback if the function is not available
        voter = w3.eth.contract(
            address=w3.to_checksum_address(VOTER_ADDRESS),
            abi=VOTER_ABI
        )
        try:
            return voter.functions.current_period().call(block_identifier=block_number)
        except Exception as e:
            logger.error(f"Failed to get epoch from block: {e}")
            return None

def fetch_all_pools_at_block(w3, block_number):
    """
    Fetch all pools from LpSugar contract at a specific block
    
    Args:
        w3: Web3 instance
        block_number: Block number
    
    Returns:
        List of pools
    """
    logger.info(f"Fetching all pools via LpSugar at block {block_number}...")
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
            # Fix parameter order: PAGE_SIZE first, then offset
            batch = lp_sugar.functions.all(PAGE_SIZE, offset).call(block_identifier=block_number)
            if not batch:
                break
            all_pools.extend(batch)
            offset += PAGE_SIZE
            if len(batch) < PAGE_SIZE:
                break
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
    
    logger.info(f"Retrieved {len(formatted_pools)} pools at block {block_number}")
    return formatted_pools

def filter_votable_pools(pools):
    """
    Filter pools to only include votable ones
    
    Args:
        pools: List of pools
    
    Returns:
        Filtered list of votable pools
    """
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

def enrich_pools(w3, pools, block_number):
    """
    Add token symbols to pools
    
    Args:
        w3: Web3 instance
        pools: List of pools
        block_number: Block number
    
    Returns:
        Enriched pool data
    """
    logger.info(f"Enriching pools with token symbols at block {block_number}...")
    enriched_pools = []
    zero_addr = "0x0000000000000000000000000000000000000000"
    
    for pool in pools:
        symbol = pool.get("symbol", "") or ""
        if not symbol or symbol.lower().startswith("0x"):
            token0_addr = pool.get("token0", zero_addr)
            token1_addr = pool.get("token1", zero_addr)
            
            token0_symbol = get_token_symbol(w3, token0_addr) or token0_addr[:6]
            token1_symbol = get_token_symbol(w3, token1_addr) or token1_addr[:6]
            
            if token0_symbol and token1_symbol:
                symbol = f"{token0_symbol}/{token1_symbol}"
        
        pool["symbol"] = symbol
        enriched_pools.append(pool)
    
    logger.info(f"Enriched {len(enriched_pools)} pools")
    return enriched_pools

def fetch_fees_and_bribes_at_block(w3, pool_info, contract_prices, block_number):
    """
    Fetch fees and bribes data from RewardsSugar at a specific block
    
    Args:
        w3: Web3 instance
        pool_info: Dictionary of pool information
        contract_prices: Dictionary of token prices
        block_number: Block number
    
    Returns:
        List of pools with fees and bribes data
    """
    logger.info(f"Fetching fees and bribes data at block {block_number}...")
    rewards_sugar = w3.eth.contract(
        address=w3.to_checksum_address(REWARDS_SUGAR_ADDR),
        abi=REWARDS_SUGAR_ABI
    )
    
    results = []
    ZERO = "0x0000000000000000000000000000000000000000"
    
    for pool_addr, info in pool_info.items():
        try:
            # Use the same function name as in the original fetch_votes.py
            ep_arr = rewards_sugar.functions.epochsByAddress(1, 0, w3.to_checksum_address(pool_addr)).call(block_identifier=block_number)
        except ContractLogicError:
            continue
        if not ep_arr:
            continue
        
        # The first epoch should be the current one
        ep = ep_arr[0]
        ts = ep[0]
        bribes_arr = ep[4]
        fees_arr = ep[5]
        
        # Process fees
        fee0_amt = 0
        fee1_amt = 0
        fees_usd = Decimal(0)
        
        for fee in fees_arr:
            token = fee[0].lower()
            amount = fee[1]
            
            if token == info["token0"].lower():
                fee0_amt = amount
                decimals = get_token_decimals(w3, token)
                amount_human = Decimal(amount) / Decimal(10**decimals)
                price = contract_prices.get(token, Decimal(0))
                fees_usd += amount_human * price
            elif token == info["token1"].lower():
                fee1_amt = amount
                decimals = get_token_decimals(w3, token)
                amount_human = Decimal(amount) / Decimal(10**decimals)
                price = contract_prices.get(token, Decimal(0))
                fees_usd += amount_human * price
        
        # Process bribes
        bribes_usd = Decimal(0)
        bribe_list = []
        
        for bribe in bribes_arr:
            token = bribe[0].lower()
            amount = bribe[1]
            
            if token != ZERO:
                decimals = get_token_decimals(w3, token)
                amount_human = Decimal(amount) / Decimal(10**decimals)
                price = contract_prices.get(token, Decimal(0))
                bribe_value = amount_human * price
                bribes_usd += bribe_value
                
                symbol = get_token_symbol(w3, token) or token[:8]
                bribe_list.append({
                    "token": token,
                    "symbol": symbol,
                    "amount": float(amount),
                    "amount_human": float(amount_human),
                    "value_usd": float(bribe_value)
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
        
        results.append({
            "pool": pool_addr,
            "symbol": info["symbol"],
            "type": info["type"],
            "fee0_amount": fee0_amt,
            "fee1_amount": fee1_amt,
            "fees_usd": float(fees_usd),
            "bribes_usd": float(bribes_usd),
            "bribes": bribe_list,
            "total_usd": float(total_usd),
            "token0_price": float(token0_price),
            "token1_price": float(token1_price),
            "reserve0": float(reserve0),
            "reserve1": float(reserve1),
            "tvl": float(tvl)
        })
    
    results.sort(key=lambda x: x["total_usd"], reverse=True)
    
    logger.info(f"Processed fees and bribes for {len(results)} pools")
    return results

def fetch_relay_votes_at_block(w3, enriched_pools, block_number):
    """
    Fetch relay votes data at a specific block
    
    Args:
        w3: Web3 instance
        enriched_pools: List of enriched pools
        block_number: Block number
    
    Returns:
        List of relay votes
    """
    logger.info(f"Fetching relay votes data at block {block_number}...")
    
    relay_account = os.getenv("RELAY_ACCOUNT")
    relay_sugar_address = os.getenv("RELAY_SUGAR_ADDRESS")
    
    if not relay_account or not relay_sugar_address:
        logger.warning("Missing RELAY_ACCOUNT or RELAY_SUGAR_ADDRESS, skipping relay votes fetch")
        return []
    
    # Create a mapping of pool addresses to symbols for quick lookup
    pool_symbols = {p["lp"].lower(): p.get("symbol", "") for p in enriched_pools}
    
    try:
        # Load the RelaySugar ABI and create contract instance
        relay_sugar = w3.eth.contract(
            address=w3.to_checksum_address(relay_sugar_address),
            abi=RELAY_SUGAR_ABI
        )
        
        # Fetch all relays for the account at the specific block
        relays_raw = relay_sugar.functions.all(w3.to_checksum_address(relay_account)).call(block_identifier=block_number)
        logger.info(f"Retrieved {len(relays_raw)} Relay entries at block {block_number}")
        
        parsed_relays = []
        for raw in relays_raw:
            relay_pool = raw[1].lower()
            relay_pool_clean = relay_pool if w3.is_address(relay_pool) else None
            
            if relay_pool_clean:
                relay_addr = raw[0].lower()
                tokenId = raw[3]
                weight = int(raw[4]) if raw[4] is not None else 0
                amount = int(raw[5]) if raw[5] is not None else 0
                
                # Get symbol from mapping or fallback to address
                symbol = pool_symbols.get(relay_pool, relay_pool[:10] + "...")
                
                # Add to parsed relays
                parsed_relays.append({
                    "relay_addr": relay_addr,
                    "pool_addr": relay_pool_clean,
                    "tokenId": tokenId,
                    "weight": weight,
                    "voting_amount_raw": amount,
                    "voting_amount": amount / 1e18,
                    "symbol": symbol
                })
        
        # Sort by voting amount
        parsed_relays.sort(key=lambda x: Decimal(str(x["voting_amount_raw"])), reverse=True)
        
        logger.info(f"Processed {len(parsed_relays)} relays at block {block_number}")
        return parsed_relays
    
    except Exception as e:
        logger.error(f"Error fetching relay votes: {e}")
        return []

def build_relay_totals(relays):
    """
    Sum relay weights per pool
    
    Args:
        relays: List of relay votes
    
    Returns:
        Dictionary of relay totals by pool
    """
    out = {}
    for r in relays:
        pool = r["pool_addr"]
        amount = r["voting_amount_raw"]
        out[pool] = out.get(pool, 0) + amount
    return out

def create_votes_dashboard_at_block(w3, pools, block_number, relays=None):
    """
    Create final votes dashboard with on-chain weights at a specific block
    
    Args:
        w3: Web3 instance
        pools: List of pools with fees/bribes
        block_number: Block number
        relays: List of relay votes
    
    Returns:
        Dashboard data
    """
    logger.info(f"Creating votes dashboard at block {block_number}...")
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
        total_weight = voter.functions.totalWeight().call(block_identifier=block_number)
    except Exception as e:
        logger.error(f"Error getting total weight: {e}")
    
    # Get our NFT balance
    our_nft_weight = 0
    try:
        our_nft_weight = ve.functions.balanceOfNFT(NFT_ID).call(block_identifier=block_number)
    except Exception as e:
        logger.error(f"Error getting NFT balance: {e}")
    
    logger.info(f"Total voting weight at block {block_number}: {total_weight}")
    logger.info(f"Our NFT weight at block {block_number}: {our_nft_weight}")
    
    # Calculate relay totals
    relay_totals = {}
    if relays:
        relay_totals = build_relay_totals(relays)
    
    # Add weights to pools
    augmented_pools = []
    pool_summed_weights = Decimal(0)
    for entry in pools:
        pool_addr = entry["pool"].lower()
        
        # Get on-chain weight for this pool
        pool_weight = 0
        try:
            pool_weight = voter.functions.weights(w3.to_checksum_address(pool_addr)).call(block_identifier=block_number)
        except Exception as e:
            logger.error(f"Error getting weight for pool {pool_addr}: {e}")
        
        # Get relay weight for this pool
        relay_weight = relay_totals.get(pool_addr, 0)
        
        # Calculate percentage of total weights
        if total_weight > 0:
            weight_pct = (pool_weight / total_weight) * 100
        else:
            weight_pct = 0
        
        # Calculate what percentage our vote would be
        our_vote_impact = 0
        if total_weight > 0 and our_nft_weight > 0:
            our_vote_impact = (our_nft_weight / total_weight) * 100
        
        # Add to running total for validation
        pool_summed_weights += Decimal(pool_weight)
        
        # Create augmented pool entry
        augmented_entry = {**entry}
        augmented_entry["on_chain_weight"] = pool_weight
        augmented_entry["on_chain_weight_pct"] = float(weight_pct)
        augmented_entry["relay_weight"] = relay_weight
        augmented_entry["total_weight"] = pool_weight + relay_weight
        augmented_entry["our_vote_impact"] = float(our_vote_impact)
        
        augmented_pools.append(augmented_entry)
    
    logger.info(f"Sum of all pool weights at block {block_number}: {pool_summed_weights}")
    
    # Get epoch at this block
    epoch = estimate_epoch_from_block(w3, block_number)
    
    # Create dashboard
    dashboard = {
        "block_number": block_number,
        "epoch": epoch,
        "total_weight": float(total_weight),
        "our_voting_power": float(our_nft_weight),
        "pool_summed_weights": float(pool_summed_weights),
        "pools": augmented_pools
    }
    
    # Add relay information if available
    if relays:
        dashboard["relays"] = relays
    
    # Sort by total_usd
    dashboard["pools"].sort(key=lambda x: x["total_usd"], reverse=True)
    
    logger.info(f"Created dashboard at block {block_number} with {len(augmented_pools)} pools")
    return dashboard

def run_fetch_votes_previous_epoch():
    """
    Main function to fetch votes data for previous epoch
    """
    logger.info("🔄 Starting previous epoch votes data fetch...")
    
    # Step 1: Get the timestamp for previous epoch (just before epoch change)
    timestamp = get_previous_epoch_timestamp()
    date_str = datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc).strftime('%Y%m%d')
    
    # Step 2: Get the block number for that timestamp
    block_number = get_block_by_timestamp(timestamp)
    if not block_number:
        logger.error("❌ Failed to get block number for timestamp")
        return
    
    logger.info(f"📊 Processing votes data at block {block_number} (timestamp: {timestamp})")
    
    w3 = get_web3()
    if not w3:
        logger.error("❌ Failed to initialize Web3")
        return
    
    # Step 3: Fetch all pools at that block
    all_pools = fetch_all_pools_at_block(w3, block_number)
    
    # Step 4: Filter votable pools
    votable_pools = filter_votable_pools(all_pools)
    
    # Step 5: Enrich pools with symbols
    enriched_pools = enrich_pools(w3, votable_pools, block_number)
    
    # Step 6: Get token list and fetch CoinGecko IDs
    tokens = set()
    for p in enriched_pools:
        t0 = p.get("token0", "").lower()
        t1 = p.get("token1", "").lower()
        if w3.is_address(t0):
            tokens.add(w3.to_checksum_address(t0).lower())
        if w3.is_address(t1):
            tokens.add(w3.to_checksum_address(t1).lower())
    
    token_to_id = fetch_coingecko_token_ids(tokens)
    
    # Step 7: Fetch prices and calculate fees/bribes
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
    
    # Step 8: Fetch fees and bribes at block
    pools_with_fees = fetch_fees_and_bribes_at_block(w3, pool_info, contract_prices, block_number)
    
    # Step 9: Fetch relay votes at block
    relay_data = fetch_relay_votes_at_block(w3, enriched_pools, block_number)
    
    # Step 10: Estimate epoch
    epoch = estimate_epoch_from_block(w3, block_number)
    
    # Step 11: Create votes dashboard
    dashboard = create_votes_dashboard_at_block(w3, pools_with_fees, block_number, relay_data)
    
    # Add timestamp and date information
    dashboard["timestamp"] = timestamp
    dashboard["date"] = datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    
    # Create directory if it doesn't exist
    os.makedirs(PREVIOUS_VOTES_DIR, exist_ok=True)
    
    # Save with date and epoch in filename
    dated_path = f"{PREVIOUS_VOTES_DIR}/votes_dashboard_{epoch}_{date_str}.json"
    save_json(dashboard, dated_path)
    
    # Also save as the default file
    default_path = f"{PREVIOUS_VOTES_DIR}/votes_dashboard_previous.json"
    save_json(dashboard, default_path)
    
    logger.info(f"✅ Previous epoch votes data saved to {dated_path} and {default_path}")
    
    return dashboard

if __name__ == "__main__":
    run_fetch_votes_previous_epoch()
