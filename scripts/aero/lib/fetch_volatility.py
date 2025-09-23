#!/usr/bin/env python3
# filepath: d:\Pyth\pythfarms\scripts\aero\lib\fetch_volatility.py

import os
import json
import time
import logging
import requests
import datetime
from decimal import Decimal
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()

# Constants
GECKOTERMINAL_API_URL = 'https://api.geckoterminal.com/api/v2'
DASHBOARD_PATH = "input_data/aero/votes_dashboard.json"
VOLATILITY_DATA_PATH = "volatility_data/aero/volatility_data.json"

def save_json(data, path):
    """Save data to a JSON file"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)
    logger.info(f"✅ Saved data to {path}")

def load_json(path):
    """Load data from a JSON file"""
    if not os.path.exists(path):
        logger.warning(f"⚠️ File not found: {path}")
        return None
    with open(path, 'r') as f:
        return json.load(f)

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
    
    # Also keep track of simple high/low for reference
    period_high = max(closes)
    period_low = min(closes)
    
    # Return detailed metrics for analysis
    return {
        'high_close': period_high,
        'low_close': period_low,
        'current_price': current_close,
        'mean_price': mean_price,
        'std_dev': round(std_dev, 6),
        'volatility_percentage': round(volatility_percentage, 4),  # 4 decimal places for percentage
        'debug': {
            'num_samples': len(closes),
            'variance': round(variance, 6)
        }
    }

def fetch_geckoterminal_volatility(pool_address, network="base"):
    """Fetch and calculate 7-day volatility metrics from GeckoTerminal OHLCV data"""
    try:
        url = f"{GECKOTERMINAL_API_URL}/networks/{network}/pools/{pool_address}/ohlcv/hour"
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
            },
            'last_updated': datetime.datetime.now().isoformat()
        }
        
        return volatility
        
    except Exception as e:
        logger.warning(f"Failed to fetch GeckoTerminal data for pool {pool_address}: {e}")
        return None

def fetch_pools_from_dashboard():
    """Load pools from the latest votes dashboard"""
    dashboard = load_json(DASHBOARD_PATH)
    if not dashboard:
        logger.error("❌ Failed to load votes dashboard")
        return []
    
    return dashboard.get('pools', [])

def run_fetch_volatility(max_pools=None, rate_limit_seconds=2, force_update=False):
    """
    Fetch volatility data for pools in the votes dashboard
    
    Args:
        max_pools (int, optional): Maximum number of pools to process. If None, process all pools.
        rate_limit_seconds (int, optional): Seconds to wait between API calls to avoid rate limiting.
        force_update (bool, optional): If True, update all pools regardless of existing data.
    
    Returns:
        dict: Dictionary of pool addresses mapped to their volatility data
    """
    # Load existing volatility data if it exists
    existing_data = load_json(VOLATILITY_DATA_PATH) or {"pools": {}, "last_updated": None}
    
    # Get pools from the dashboard
    pools = fetch_pools_from_dashboard()
    if not pools:
        logger.error("❌ No pools found in dashboard")
        return existing_data
    
    # Sort pools by weight/votes to prioritize the most important ones
    if any('weight' in p for p in pools):
        pools.sort(key=lambda x: x.get('weight', 0), reverse=True)
    elif any('on_chain_weight' in p for p in pools):
        pools.sort(key=lambda x: x.get('on_chain_weight', 0), reverse=True)
    
    # Limit the number of pools if specified
    if max_pools:
        pools = pools[:max_pools]
    
    # Process each pool
    updated_pools = existing_data.get("pools", {})
    num_updated = 0
    
    logger.info(f"🔍 Fetching volatility data for {len(pools)} pools...")
    
    for i, pool in enumerate(pools):
        pool_addr = pool.get('pool', '').lower()
        pool_symbol = pool.get('symbol', 'Unknown')
        
        # Skip if we already have recent data for this pool and not forcing update
        if not force_update and pool_addr in updated_pools:
            last_updated = updated_pools[pool_addr].get('last_updated')
            if last_updated:
                try:
                    update_time = datetime.datetime.fromisoformat(last_updated)
                    now = datetime.datetime.now()
                    # Skip if updated within the last 12 hours
                    if (now - update_time).total_seconds() < 12 * 3600:
                        logger.info(f"Skipping {pool_symbol} ({pool_addr}): already updated recently")
                        continue
                except (ValueError, TypeError):
                    pass
        
        logger.info(f"Processing {i+1}/{len(pools)}: {pool_symbol} ({pool_addr})")
        
        # Rate limiting
        if i > 0:
            time.sleep(rate_limit_seconds)
        
        # Fetch volatility data
        volatility_data = fetch_geckoterminal_volatility(pool_addr)
        
        if volatility_data:
            updated_pools[pool_addr] = {
                **volatility_data,
                'symbol': pool_symbol
            }
            num_updated += 1
            logger.info(f"✅ Updated volatility data for {pool_symbol}: {volatility_data['price_range']['volatility_percentage']}%")
        else:
            # Keep existing data if fetch failed
            logger.warning(f"⚠️ Failed to fetch volatility data for {pool_symbol}")
    
    # Update the result
    result = {
        "pools": updated_pools,
        "last_updated": datetime.datetime.now().isoformat(),
        "stats": {
            "total_pools": len(updated_pools),
            "updated_pools": num_updated
        }
    }
    
    # Save the result
    save_json(result, VOLATILITY_DATA_PATH)
    
    # Also save a dated version
    date_str = datetime.datetime.now().strftime("%Y%m%d")
    dated_path = f"volatility_data/aero/volatility_data_{date_str}.json"
    save_json(result, dated_path)
    
    logger.info(f"✅ Volatility data saved for {len(updated_pools)} pools ({num_updated} updated)")
    
    return result

if __name__ == "__main__":
    run_fetch_volatility(max_pools=30)
