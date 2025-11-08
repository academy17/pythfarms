#!/usr/bin/env python3
# filepath: d:\Pyth\pythfarms\scripts\aero\lib\fetch_volatility.py
"""
Aerodrome Pool Volatility Data Fetcher

This module fetches historical price data for Aerodrome pools and calculates volatility metrics
using standard deviation of hourly closing prices over a 7-day period. It requires a votes_dashboard to exist.

DATA SOURCE:
    - GeckoTerminal API (https://api.geckoterminal.com/api/v2)
    - Network: Base
    - Timeframe: 168 hours (7 days) of hourly OHLCV data

INPUT:
    - votes_dashboard.json: Pool list to fetch volatility for
      Location: input_data/aero/votes_dashboard.json

OUTPUT:
    - volatility_data.json: Volatility metrics for all pools
      Location: volatility_data/aero/volatility_data.json
    - volatility_data_YYYYMMDD.json: Dated backup copy
      Location: volatility_data/aero/volatility_data_YYYYMMDD.json

VOLATILITY CALCULATION:
    Using standard deviation of closing prices:
    1. Fetch 168 hourly candles (7 days)
    2. Calculate mean price
    3. Calculate deviations from mean
    4. Standard deviation = √(Σ(deviation²) / n)
    5. Volatility % = (std_dev / current_price) × 100

OUTPUT FORMAT:
    {
        "pools": {
            "0x...pool_address": {
                "symbol": "vAMM-WETH/USDC",
                "current_price": 0.0003145,
                "price_range": {
                    "high": 0.0003200,
                    "low": 0.0003100,
                    "range": 0.0001,
                    "mid_price": 0.0003150,
                    "volatility_percentage": 6.42,  # Used by optimizer
                    "std_dev": 0.0000202,
                    "metrics": {
                        "num_samples": 168,
                        "variance": 4.08e-10
                    }
                },
                "last_updated": "2025-10-22T10:30:00.123456"
            }
        },
        "last_updated": "2025-10-22T10:35:00.123456",
        "stats": {
            "total_pools": 254,
            "updated_pools": 45
        }
    }

SMART CACHING:
    - Only re-fetches data if it's older than 12 hours
    - Use force_update=True to override and update all pools
    - Skips pools with recent data to respect API rate limits

RATE LIMITING:
    - Default: 2 seconds between API calls
    - Adjustable via rate_limit_seconds parameter
    - Processes pools in order of importance (highest weight first)

USAGE:

Fetch all pools (respects 12-hour cache):
    python scripts/aero/aero_manager.py volatility

Fetch top 50 pools only:
    from scripts.aero.lib.fetch_volatility import run_fetch_volatility
    run_fetch_volatility(max_pools=50)

Force update all pools:
    run_fetch_volatility(force_update=True)

Custom rate limiting:
    run_fetch_volatility(rate_limit_seconds=3)

INTEGRATION WITH OPTIMIZER:
    The optimizer reads volatility_percentage from this file when run with:
    python scripts/aero/aero_manager.py optimize --with-volatility --gamma 1.0
    
    Higher volatility_percentage = Higher penalty on voter rewards
    (LP rewards are not affected by volatility)

RECOMMENDED SCHEDULE:
    - Run once per week (Sunday before voting)

"""

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
DEXSCREENER_API_URL = 'https://api.dexscreener.com/latest/dex/pairs'
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

def fetch_dexscreener_price(pool_address, chain_id="base"):
    """Fetch current price from DexScreener API
    
    Args:
        pool_address: The pool/pair address
        chain_id: Chain identifier (e.g., "base", "sonic")
    
    Returns:
        float: Current price, or None if fetch fails
    """
    try:
        url = f"{DEXSCREENER_API_URL}/{chain_id}/{pool_address}"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        
        # DexScreener returns a 'pair' or 'pairs' structure
        pair_data = data.get('pair') or (data.get('pairs', [{}])[0] if data.get('pairs') else None)
        
        if pair_data and 'priceUsd' in pair_data:
            price = float(pair_data['priceUsd'])
            logger.debug(f"DexScreener price for {pool_address}: ${price}")
            return price
        else:
            logger.warning(f"No price data found in DexScreener response for {pool_address}")
            return None
            
    except Exception as e:
        logger.warning(f"Failed to fetch DexScreener price for {pool_address}: {e}")
        return None

def calculate_volatility_metrics(candles):
    """Calculate volatility and volume metrics from OHLCV candles
    Each candle is [timestamp, open, high, low, close, volume]
    
    Handles cases where fewer candles are available than requested.
    """
    if not candles or len(candles) < 2:  # Need at least 2 candles
        logger.warning(f"Insufficient candle data: only {len(candles) if candles else 0} candles available")
        return None
    
    # Log if we have less data than expected
    if len(candles) < 168:  # Less than 1 week
        logger.warning(f"⚠️ Only {len(candles)} hours of data available (less than 1 week)")
    elif len(candles) < 672:  # Less than 4 weeks
        logger.info(f"ℹ️ {len(candles)} hours of data available (less than 4 weeks)")
        
    # Extract close prices and volumes (newest first)
    closes = [float(candle[4]) for candle in candles]  # close is at index 4
    volumes = [float(candle[5]) for candle in candles]  # volume is at index 5
    
    current_close = closes[0]  # most recent close
    
    # Price metrics
    mean_price = sum(closes) / len(closes)
    deviations = [price - mean_price for price in closes]
    squared_deviations = [dev ** 2 for dev in deviations]
    variance = sum(squared_deviations) / len(squared_deviations)
    std_dev = variance ** 0.5
    volatility_percentage = (std_dev / current_close) * 100 if current_close > 0 else 0
    
    # Price range metrics
    period_high = max(closes)
    period_low = min(closes)
    
    # Volume metrics
    total_volume = sum(volumes)
    avg_volume = total_volume / len(volumes)
    max_volume = max(volumes)
    # Get lowest non-zero volume to avoid skewing metrics with zero-volume periods
    non_zero_volumes = [v for v in volumes if v > 0]
    min_volume = min(non_zero_volumes) if non_zero_volumes else 0
    
    # Weekly breakdown - adapt to available data
    # Calculate how many complete weeks we can get
    week_hours = 168
    available_hours = len(volumes)
    num_complete_weeks = min(4, available_hours // week_hours)
    
    weekly = []
    for w in range(num_complete_weeks):
        start = w * week_hours
        end = start + week_hours
        week_slice = volumes[start:end]
        if not week_slice:
            break
        w_total = sum(week_slice)
        w_avg = (w_total / len(week_slice)) if week_slice else 0
        weekly.append({
            'week_index': w + 1,
            'total_volume': round(w_total, 2),
            'avg_hourly': round(w_avg, 2),
            'hours_sampled': len(week_slice)
        })
    
    # If we have partial data for another week, include it
    if num_complete_weeks < 4 and available_hours > num_complete_weeks * week_hours:
        start = num_complete_weeks * week_hours
        week_slice = volumes[start:]
        if week_slice:
            w_total = sum(week_slice)
            w_avg = (w_total / len(week_slice)) if week_slice else 0
            weekly.append({
                'week_index': num_complete_weeks + 1,
                'total_volume': round(w_total, 2),
                'avg_hourly': round(w_avg, 2),
                'hours_sampled': len(week_slice),
                'partial': True  # Flag to indicate incomplete week
            })
    
    # Return detailed metrics for analysis
    return {
        'high_close': period_high,
        'low_close': period_low,
        'current_price': current_close,
        'mean_price': mean_price,
        'std_dev': round(std_dev, 6),
        'volatility_percentage': round(volatility_percentage, 4),
        'volume_metrics': {
            'total_volume': round(total_volume, 2),
            'average_volume': round(avg_volume, 2),
            'highest_volume': round(max_volume, 2),
            'lowest_volume': round(min_volume, 2),
            'sampling_hours': len(volumes),
            'weekly': weekly,
            'data_completeness': {
                'hours_available': available_hours,
                'hours_requested': 672,
                'completeness_pct': round((available_hours / 672) * 100, 2)
            }
        },
        'debug': {
            'num_samples': len(closes),
            'variance': round(variance, 6)
        }
    }


def fetch_geckoterminal_volatility(pool_address, network="base", use_monthly=False):
    """Fetch and calculate volatility metrics from GeckoTerminal OHLCV data
    
    Args:
        pool_address: The pool address to fetch data for
        network: Network name (default: "base")
        use_monthly: If True, fetch 30-day (720 hour) data instead of 7-day (168 hour)
    """
    try:
        # Default to 672 hours (4 weeks). If use_monthly is requested keep 720.
        hours_limit = 720 if use_monthly else 672
        timeframe_desc = "30-day" if use_monthly else "28-day"
        
        url = f"{GECKOTERMINAL_API_URL}/networks/{network}/pools/{pool_address}/ohlcv/hour"
        params = {
            'aggregate': '1',
            'limit': str(hours_limit)  # 720 hours (30 days) or 168 hours (7 days)
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
        
        # Fetch current price from DexScreener for more accuracy
        dexscreener_price = fetch_dexscreener_price(pool_address, chain_id=network)
        current_price = dexscreener_price if dexscreener_price is not None else metrics['current_price']
        
        # If we got a DexScreener price, recalculate volatility percentage with it
        if dexscreener_price is not None:
            std_dev = metrics['std_dev']
            volatility_percentage = (std_dev / current_price) * 100 if current_price > 0 else metrics['volatility_percentage']
        else:
            volatility_percentage = metrics['volatility_percentage']
            
        # Construct response with detailed metrics including volume
        volatility = {
            'current_price': current_price,
            'price_source': 'dexscreener' if dexscreener_price is not None else 'geckoterminal',
            'price_range': {
                'high': metrics['high_close'],
                'low': metrics['low_close'],
                'range': metrics['high_close'] - metrics['low_close'],
                'mid_price': metrics['mean_price'],
                'volatility_percentage': round(volatility_percentage, 4),
                'std_dev': metrics['std_dev'],
                'metrics': {
                    'num_samples': metrics['debug']['num_samples'],
                    'variance': metrics['debug']['variance']
                }
            },
            'volume': {
                'total': metrics['volume_metrics']['total_volume'],
                'avg_hourly': metrics['volume_metrics']['average_volume'],
                'highest_hourly': metrics['volume_metrics']['highest_volume'],
                'lowest_hourly': metrics['volume_metrics']['lowest_volume'],
                'hours_sampled': metrics['volume_metrics']['sampling_hours'],
                'weekly': metrics['volume_metrics'].get('weekly', [])
            },
            'timeframe': timeframe_desc,
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

def run_fetch_volatility(max_pools=None, rate_limit_seconds=2, force_update=False, use_monthly=False):
    """
    Fetch volatility data for pools in the votes dashboard
    
    Args:
        max_pools (int, optional): Maximum number of pools to process. If None, process all pools.
        rate_limit_seconds (int, optional): Seconds to wait between API calls to avoid rate limiting.
        force_update (bool, optional): If True, update all pools regardless of existing data.
        use_monthly (bool, optional): If True, fetch 30-day (720 hour) volatility instead of 7-day (168 hour).
    
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
    error_occurred = False
    
    timeframe_desc = "30-day" if use_monthly else "28-day"
    logger.info(f"🔍 Fetching {timeframe_desc} volatility data for {len(pools)} pools...")
    
    try:
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
            volatility_data = fetch_geckoterminal_volatility(pool_addr, use_monthly=use_monthly)
            
            if volatility_data:
                updated_pools[pool_addr] = {
                    **volatility_data,
                    'symbol': pool_symbol
                }
                num_updated += 1
                vol_metrics = volatility_data['volume']
                logger.info(f"✅ Updated {pool_symbol}:")
                logger.info(f"   📈 Volatility: {volatility_data['price_range']['volatility_percentage']}%")
                logger.info(f"   💧 Volume: {vol_metrics['total']:.2f} total (avg {vol_metrics['avg_hourly']:.2f}/hr)")
                
                # Save progress incrementally after each successful fetch
                # This ensures data is not lost if the process is interrupted
                intermediate_result = {
                    "pools": updated_pools,
                    "last_updated": datetime.datetime.now().isoformat(),
                    "stats": {
                        "total_pools": len(updated_pools),
                        "updated_pools": num_updated,
                        "in_progress": True
                    }
                }
                save_json(intermediate_result, VOLATILITY_DATA_PATH)
                logger.info(f"💾 Progress saved ({num_updated}/{len(pools)} pools updated)")
            else:
                # Keep existing data if fetch failed
                logger.warning(f"⚠️ Failed to fetch volatility data for {pool_symbol}")
    
    except KeyboardInterrupt:
        logger.warning("\n⚠️ Process interrupted by user! Saving progress...")
        error_occurred = True
    except Exception as e:
        logger.error(f"\n❌ Error during fetch: {e}. Saving progress...")
        error_occurred = True
    
    # Update the result with final status
    result = {
        "pools": updated_pools,
        "last_updated": datetime.datetime.now().isoformat(),
        "stats": {
            "total_pools": len(updated_pools),
            "updated_pools": num_updated,
            "in_progress": False
        }
    }
    
    # Save the final result
    save_json(result, VOLATILITY_DATA_PATH)
    
    # Also save a dated version
    date_str = datetime.datetime.now().strftime("%Y%m%d")
    dated_path = f"volatility_data/aero/volatility_data_{date_str}.json"
    save_json(result, dated_path)
    
    logger.info(f"✅ Volatility data saved for {len(updated_pools)} pools ({num_updated} updated)")
    
    return result

if __name__ == "__main__":
    run_fetch_volatility(max_pools=100)
