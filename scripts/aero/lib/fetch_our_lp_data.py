#!/usr/bin/env python3
import os
import json
import logging
from decimal import Decimal, getcontext
from web3 import Web3
from web3.exceptions import ContractLogicError
from dotenv import load_dotenv
import requests

# Local function definitions instead of imports to prevent circular imports
def load_abi(name):
    """Load ABI from file"""
    with open(f"abi/aero/{name}.json") as f:
        return json.load(f)
        
def get_web3():
    """Initialize and return a Web3 instance"""
    RPC_URL = os.getenv("RPC_URL")
    if not RPC_URL:
        logger.error("❌ RPC_URL not set in environment")
        return None
    
    return Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 60}))

# Cache for token data
_token_decimals_cache = {}
_token_symbol_cache = {}

def get_token_decimals(w3, token_addr):
    """Get token decimals, with caching"""
    key = token_addr.lower()
    if key in _token_decimals_cache:
        return _token_decimals_cache[key]
    try:
        c = w3.eth.contract(address=w3.to_checksum_address(key), abi=[{
            "constant": True,
            "inputs": [],
            "name": "decimals",
            "outputs": [{"name": "", "type": "uint8"}],
            "type": "function"
        }])
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
        c = w3.eth.contract(address=w3.to_checksum_address(key), abi=[{
            "constant": True,
            "inputs": [],
            "name": "symbol",
            "outputs": [{"name": "", "type": "string"}],
            "type": "function"
        }])
        s = c.functions.symbol().call()
    except Exception:
        s = None
    _token_symbol_cache[key] = s
    return s

def fetch_coingecko_token_ids(tokens):
    """Map token addresses to CoinGecko IDs"""
    logger.info("Fetching CoinGecko token IDs...")
    url = "https://api.coingecko.com/api/v3/coins/list?include_platform=true"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    all_coins = resp.json()
    
    mapping = {}
    for coin in all_coins:
        platforms = coin.get("platforms") or {}
        base_addr = platforms.get("base")
        if base_addr and base_addr.lower() in tokens:
            mapping[base_addr.lower()] = coin.get("id")
    
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
            resp = requests.get("https://api.coingecko.com/api/v3/simple/price", params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            
            # Map CoinGecko IDs back to token addresses
            for token_addr, cg_id in token_to_id.items():
                if cg_id in data and "usd" in data[cg_id]:
                    prices[token_addr] = Decimal(str(data[cg_id]["usd"]))
        except Exception as e:
            logger.error(f"Error fetching prices from CoinGecko: {e}")
    
    return prices

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Set precision for decimal calculations
getcontext().prec = 28

# Load environment variables
load_dotenv()

# Constants
RPC_URL = os.getenv("RPC_URL")
LP_SUGAR_ADDRESS = os.getenv("LP_SUGAR_ADDRESS")
AERO_SAFE_ADDRESS = os.getenv("AERO_SAFE_ADDRESS", "").strip()  # Strip whitespace
LP_ADDRESSES = os.getenv("LP_ADDRESSES", "").strip()  # New: support for multiple addresses
PAGE_SIZE = int(os.getenv("PAGE_SIZE", 200))

# Process addresses
lp_address_list = []

# First add the AERO_SAFE_ADDRESS if present
if AERO_SAFE_ADDRESS:
    # Remove 0x prefix if present
    clean_addr = AERO_SAFE_ADDRESS.replace('0x', '').lower()
    # Check if it's a valid hex string of correct length
    if len(clean_addr) != 40 or not all(c in '0123456789abcdef' for c in clean_addr):
        logger.error(f"❌ Invalid address format: {AERO_SAFE_ADDRESS}")
        logger.error("Address should be 40 hex characters (excluding 0x prefix)")
    else:
        lp_address_list.append(f"0x{clean_addr}")

# Then process the comma-separated list of LP_ADDRESSES
if LP_ADDRESSES:
    for addr in LP_ADDRESSES.split(','):
        addr = addr.strip()
        if not addr:
            continue
        # Remove 0x prefix if present
        clean_addr = addr.replace('0x', '').lower()
        # Check if it's a valid hex string of correct length
        if len(clean_addr) != 40 or not all(c in '0123456789abcdef' for c in clean_addr):
            logger.error(f"❌ Invalid address format in LP_ADDRESSES: {addr}")
            logger.error("Address should be 40 hex characters (excluding 0x prefix)")
        else:
            lp_address_list.append(f"0x{clean_addr}")

# Deduplicate addresses
lp_address_list = list(set(lp_address_list))

def fetch_our_positions(w3, addresses=None):
    """Fetch all our LP positions using LpSugar for multiple addresses
    
    Args:
        w3: Web3 instance
        addresses: List of addresses to fetch positions for. If None, uses lp_address_list
        
    Returns:
        List of formatted position dictionaries
    """
    if addresses is None:
        addresses = lp_address_list
    
    if not addresses:
        logger.error("❌ No valid addresses to fetch positions for")
        return []
    
    logger.info(f"🔍 Fetching LP positions via LpSugar for {len(addresses)} addresses...")
    
    lp_sugar = w3.eth.contract(
        address=w3.to_checksum_address(LP_SUGAR_ADDRESS),
        abi=load_abi("LpSugar")
    )
    
    # Extract field names from ABI for positions function
    fn_abi = next((item for item in lp_sugar.abi if item.get("name") == "positions" and item.get("type") == "function"), None)
    if not fn_abi:
        logger.error("❌ Could not find 'positions' function in LpSugar ABI")
        return []
    
    field_names = [c["name"] for c in fn_abi["outputs"][0]["components"]]
    
    # Fetch positions for each address
    all_formatted_positions = []
    
    for address in addresses:
        logger.info(f"Fetching positions for {address}...")
        # Fetch positions in batches
        offset = 0
        address_positions = []
        
        while True:
            try:
                batch = lp_sugar.functions.positions(
                    PAGE_SIZE, 
                    offset, 
                    w3.to_checksum_address(address)
                ).call()
                
                if not batch:
                    break
                address_positions.extend(batch)
                offset += PAGE_SIZE
                
                logger.info(f"Retrieved {len(batch)} positions in batch for {address}")
            except ContractLogicError as e:
                logger.error(f"Error fetching positions for {address}: {e}")
                break
        
        # Format positions for this address
        for entry in address_positions:
            pos_dict = {}
            for name, val in zip(field_names, entry):
                # Convert bytes to hex strings
                if isinstance(val, (bytes, bytearray)):
                    pos_dict[name] = "0x" + val.hex()
                else:
                    pos_dict[name] = val
            
            # Add the address this position belongs to
            pos_dict['owner_address'] = address
            
            all_formatted_positions.append(pos_dict)
    
    logger.info(f"✅ Retrieved {len(all_formatted_positions)} total positions across {len(addresses)} addresses")
    return all_formatted_positions

def calculate_positions_value(w3, positions):
    """Calculate USD value of positions"""
    if not positions:
        return []
    
    # Collect all token addresses
    tokens = set()
    for pos in positions:
        # We'll need to get the pool info to get token addresses
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
        
        try:
            token0 = lp_contract.functions.token0().call().lower()
            token1 = lp_contract.functions.token1().call().lower()
            tokens.add(token0)
            tokens.add(token1)
        except Exception as e:
            logger.error(f"Error getting tokens for pool {pos['lp']}: {e}")
            continue
    
    # Get token prices
    token_to_id = fetch_coingecko_token_ids(tokens)
    token_prices = fetch_prices_from_coingecko(token_to_id)
    
    enriched_positions = []
    for pos in positions:
        try:
            # Get pool tokens
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
            
            # Get prices
            price0 = token_prices.get(token0, Decimal(0))
            price1 = token_prices.get(token1, Decimal(0))
            
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
                'total_value_usd': float(total_value_usd),
                'owner_address': pos.get('owner_address')  # Include the owner address
            }
            
            enriched_positions.append(enriched_pos)
            
        except Exception as e:
            logger.error(f"Error processing position for pool {pos['lp']}: {e}")
            continue
    
    # Sort by total value
    enriched_positions.sort(key=lambda x: x['total_value_usd'], reverse=True)
    return enriched_positions

def main():
    """Main function to fetch and display our LP positions"""
    logger.info("Starting LP position value calculation...")
    
    # Check required environment variables
    if not lp_address_list:
        logger.error("❌ No valid LP addresses found. Please set AERO_SAFE_ADDRESS or LP_ADDRESSES in environment variables")
        return
    
    if not LP_SUGAR_ADDRESS:
        logger.error("❌ LP_SUGAR_ADDRESS not set in environment variables")
        return
        
    w3 = get_web3()
    if not w3:
        logger.error("❌ Failed to connect to RPC")
        return
        
    logger.info(f"Using {len(lp_address_list)} addresses: {', '.join(lp_address_list)}")
    
    # Fetch positions
    positions = fetch_our_positions(w3)
    if not positions:
        logger.error("❌ No positions found")
        return
    
    # Calculate values
    valued_positions = calculate_positions_value(w3, positions)
    
    # Group positions by pool address
    positions_by_pool = {}
    for pos in valued_positions:
        pool_addr = pos['pool']
        if pool_addr not in positions_by_pool:
            positions_by_pool[pool_addr] = []
        positions_by_pool[pool_addr].append(pos)
    
    # Consolidate positions for the same pool
    consolidated_positions = []
    for pool_addr, pool_positions in positions_by_pool.items():
        if len(pool_positions) == 1:
            consolidated_positions.append(pool_positions[0])
        else:
            # Multiple positions in the same pool - combine them
            combined = pool_positions[0].copy()
            combined['addresses'] = [p['owner_address'] for p in pool_positions]
            
            # Sum up the values and amounts
            for key in ['amount0_human', 'amount1_human', 'value0_usd', 'value1_usd', 'total_value_usd']:
                combined[key] = sum(p[key] for p in pool_positions)
            
            consolidated_positions.append(combined)
    
    # Sort by total value
    consolidated_positions.sort(key=lambda x: x['total_value_usd'], reverse=True)
    
    # Display results
    total_value = sum(pos['total_value_usd'] for pos in consolidated_positions)
    
    print("\n================ Our LP Positions ================")
    print(f"Total Value: ${total_value:,.2f}")
    print(f"Total Addresses: {len(lp_address_list)}")
    print(f"Total Unique Pools: {len(consolidated_positions)}")
    print("--------------------------------------------------")
    print(f"{'Pool':25} {'TVL ($)':>15} {'Token 0':>12} {'Token 1':>12}")
    print("--------------------------------------------------")
    
    for pos in consolidated_positions:
        symbol = pos['symbol'][:23].ljust(23)
        value = f"${pos['total_value_usd']:,.2f}".rjust(15)
        amt0 = f"{pos['amount0_human']:.4f}".rjust(12)
        amt1 = f"{pos['amount1_human']:.4f}".rjust(12)
        print(f"{symbol} {value} {amt0} {amt1}")
    
    print("==================================================\n")

if __name__ == "__main__":
    main()
