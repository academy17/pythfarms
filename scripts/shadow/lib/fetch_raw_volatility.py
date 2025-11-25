#!/usr/bin/env python3
# filepath: scripts/shadow/lib/fetch_raw_volatility.py
"""
Shadow Raw OHLCV Data Fetcher (Incremental)

This module builds an incremental database of raw OHLCV (Open, High, Low, Close, Volume) 
data for Shadow pools on Sonic network. Instead of fetching and discarding data each time, it maintains
a growing historical dataset:

INCREMENTAL FETCHING STRATEGY:
    1. First run: Fetch last 1000 hours of OHLCV data for each pool
    2. Subsequent runs: Only fetch NEW data since last update
    3. Append new data to existing historical records
    4. Result: Continuously growing database without redundant API calls

DATA STRUCTURE:
    {
        "pools": {
            "0x...pool_address": {
                "symbol": "CL-USDC-WETH-0.131%",
                "ohlcv": [
                    {
                        "timestamp": 1699401600,
                        "datetime": "2023-11-08T00:00:00Z",
                        "open": 0.0003145,
                        "high": 0.0003200,
                        "low": 0.0003100,
                        "close": 0.0003150,
                        "volume": 125000.50
                    },
                    ... (sorted newest to oldest)
                ],
                "first_timestamp": 1699401600,
                "last_timestamp": 1702993200,
                "num_candles": 1000,
                "last_updated": "2025-11-11T10:30:00"
            }
        },
        "metadata": {
            "total_pools": 50,
            "last_updated": "2025-11-11T10:35:00",
            "data_version": "1.0"
        }
    }


PREVENTING DUPLICATES:
    - Track last_timestamp for each pool
    - Only fetch candles with timestamp > last_timestamp
    - Use timestamp as unique identifier
    - Deduplicate based on timestamp if overlaps occur

API OPTIMIZATION:
    - Initial fetch: 1000 hours per pool
    - Daily updates: ~24 hours per pool
    - Weekly updates: ~168 hours per pool
    - Dramatically reduces API calls over time


2. Rolling Window Volatility:
    # Calculate 7-day rolling volatility
    for window_start in range(0, len(closes)-168):
        window = closes[window_start:window_start+168]
        vol = calculate_volatility(window)

3. Correlation Analysis:
    # Find pools that move together
    correlations = np.corrcoef(return_matrix)

STORAGE:
    - Output: volatility_data/shadow/raw_ohlcv/*.parquet
    - Size estimate: ~1MB per 100 pools per 1000 hours

USAGE:

Initial fetch (1000 hours for top 50 pools):
    python scripts/shadow/shadow_manager.py fetch_raw_volatility --max 50

Update with new data only:
    python scripts/shadow/shadow_manager.py fetch_raw_volatility

Force refetch all data:
    python scripts/shadow/shadow_manager.py fetch_raw_volatility --force

Fetch specific number of hours initially:
    python scripts/shadow/shadow_manager.py fetch_raw_volatility --initial-hours 2000


"""

import os
import json
import time
import logging
import requests
import datetime
import pandas as pd
import numpy as np
from pathlib import Path
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()

# Constants
GECKOTERMINAL_API_URL = 'https://api.geckoterminal.com/api/v2'
DASHBOARD_PATH = "input_data/shadow/votes_dashboard.json"
RAW_OHLCV_DIR = "volatility_data/shadow/raw_ohlcv"
DEFAULT_INITIAL_HOURS = 1000  # Max allowed by GeckoTerminal API
MAX_API_LIMIT = 1000  # GeckoTerminal API absolute max per request

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

def load_pool_dataframe(pool_address):
    """Load existing OHLCV DataFrame for a pool
    
    Args:
        pool_address: Pool address (used as filename)
    
    Returns:
        pd.DataFrame or None: DataFrame with columns [timestamp, open, high, low, close, volume]
                             Index is datetime, sorted newest first
    """
    pool_file = Path(RAW_OHLCV_DIR) / f"{pool_address}.parquet"
    if pool_file.exists():
        try:
            df = pd.read_parquet(pool_file)
            logger.debug(f"Loaded {len(df)} candles for {pool_address}")
            return df
        except Exception as e:
            logger.warning(f"Failed to load {pool_file}: {e}")
            return None
    return None

def save_pool_dataframe(pool_address, df, symbol=None):
    """Save OHLCV DataFrame for a pool
    
    Args:
        pool_address: Pool address (used as filename)
        df: DataFrame with OHLCV data
        symbol: Optional pool symbol for metadata
    """
    os.makedirs(RAW_OHLCV_DIR, exist_ok=True)
    pool_file = Path(RAW_OHLCV_DIR) / f"{pool_address}.parquet"
    
    # Ensure index is datetime
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    
    # Sort newest first
    df = df.sort_index(ascending=False)
    
    # Save as parquet (compressed, efficient for time series)
    df.to_parquet(pool_file, compression='snappy')
    
    # Also save metadata
    metadata = {
        'pool_address': pool_address,
        'symbol': symbol,
        'num_candles': len(df),
        'first_timestamp': int(df.index[-1].timestamp()),
        'last_timestamp': int(df.index[0].timestamp()),
        'first_datetime': df.index[-1].isoformat(),
        'last_datetime': df.index[0].isoformat(),
        'last_updated': datetime.datetime.now().isoformat()
    }
    
    metadata_file = Path(RAW_OHLCV_DIR) / f"{pool_address}_meta.json"
    with open(metadata_file, 'w') as f:
        json.dump(metadata, f, indent=2)
    
    logger.debug(f"Saved {len(df)} candles to {pool_file}")

def fetch_geckoterminal_ohlcv(pool_address, network="sonic", limit_hours=1000, before_timestamp=None):
    """Fetch raw OHLCV data from GeckoTerminal as a DataFrame
    
    Args:
        pool_address: The pool address to fetch data for
        network: Network name (default: "sonic")
        limit_hours: Number of hours to fetch (max 1000 per API call - hard limit!)
        before_timestamp: Only fetch candles before this timestamp (for pagination)
    
    Returns:
        pd.DataFrame or None: DataFrame with columns [open, high, low, close, volume]
                             Index is DatetimeIndex (timezone-aware UTC)
                             Sorted newest first
    """
    try:
        # Enforce API limit
        if limit_hours > MAX_API_LIMIT:
            logger.warning(f"Requested {limit_hours} hours, but API max is {MAX_API_LIMIT}. Capping at {MAX_API_LIMIT}.")
            limit_hours = MAX_API_LIMIT
        
        url = f"{GECKOTERMINAL_API_URL}/networks/{network}/pools/{pool_address}/ohlcv/hour"
        params = {
            'aggregate': '1',
            'limit': str(limit_hours)
        }
        
        if before_timestamp:
            params['before_timestamp'] = str(before_timestamp)
        
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        
        # Check if we have valid OHLCV data
        ohlcv_list = data.get('data', {}).get('attributes', {}).get('ohlcv_list', [])
        if not ohlcv_list:
            logger.warning(f"No OHLCV data found for pool {pool_address}")
            return None
        
        # Convert to DataFrame
        # GeckoTerminal format: [timestamp, open, high, low, close, volume]
        df_data = []
        for candle in ohlcv_list:
            try:
                if all(x is not None for x in candle) and len(candle) >= 6:
                    df_data.append({
                        'timestamp': int(candle[0]),
                        'open': float(candle[1]),
                        'high': float(candle[2]),
                        'low': float(candle[3]),
                        'close': float(candle[4]),
                        'volume': float(candle[5])
                    })
            except (TypeError, ValueError, IndexError) as e:
                logger.warning(f"Skipping invalid candle: {e}")
                continue
        
        if not df_data:
            logger.warning(f"No valid candle data for pool {pool_address}")
            return None
        
        # Create DataFrame
        df = pd.DataFrame(df_data)
        
        # Convert timestamp to datetime index (UTC)
        df['datetime'] = pd.to_datetime(df['timestamp'], unit='s', utc=True)
        df = df.set_index('datetime')
        df = df.drop('timestamp', axis=1)
        
        # Sort newest first
        df = df.sort_index(ascending=False)
        
        logger.debug(f"Fetched {len(df)} candles for {pool_address}")
        return df
        
    except Exception as e:
        logger.warning(f"Failed to fetch GeckoTerminal OHLCV for pool {pool_address}: {e}")
        return None

def fetch_pools_from_dashboard():
    """Load pools from the latest Shadow votes dashboard"""
    dashboard = load_json(DASHBOARD_PATH)
    if not dashboard:
        logger.error("❌ Failed to load votes dashboard")
        return []
    
    # Shadow dashboard structure is different - it's a list of pools directly
    if isinstance(dashboard, list):
        return dashboard
    elif isinstance(dashboard, dict) and 'pools' in dashboard:
        return dashboard['pools']
    else:
        logger.error("❌ Unknown dashboard format")
        return []

def merge_ohlcv_dataframes(existing_df, new_df):
    """Merge new OHLCV data with existing DataFrame, avoiding duplicates
    
    Args:
        existing_df: Existing DataFrame (or None)
        new_df: New DataFrame to add
    
    Returns:
        pd.DataFrame: Merged DataFrame, deduplicated and sorted newest first
    """
    if existing_df is None or len(existing_df) == 0:
        return new_df
    
    if new_df is None or len(new_df) == 0:
        return existing_df
    
    # Concatenate
    combined = pd.concat([existing_df, new_df])
    
    # Remove duplicates (keep first occurrence, which is newest due to sort order)
    combined = combined[~combined.index.duplicated(keep='first')]
    
    # Sort newest first
    combined = combined.sort_index(ascending=False)
    
    return combined

def run_fetch_raw_volatility(max_pools=None, rate_limit_seconds=2, force_update=False, initial_hours=DEFAULT_INITIAL_HOURS):
    """
    Incrementally fetch and store raw OHLCV data for pools using pandas DataFrames
    
    Each pool is stored as a separate parquet file for efficient time-series operations.
    
    Args:
        max_pools: Maximum number of pools to process. If None, process all pools.
        rate_limit_seconds: Seconds to wait between API calls
        force_update: If True, refetch all data from scratch
        initial_hours: Number of hours to fetch on first run (max: 1000 due to API limit)
    
    Returns:
        dict: Summary statistics
    """
    # Ensure initial_hours doesn't exceed API limit
    if initial_hours > MAX_API_LIMIT:
        logger.warning(f"initial_hours ({initial_hours}) exceeds API limit ({MAX_API_LIMIT}). Setting to {MAX_API_LIMIT}.")
        initial_hours = MAX_API_LIMIT
    
    # Create output directory
    os.makedirs(RAW_OHLCV_DIR, exist_ok=True)
    
    # Get pools from dashboard
    pools = fetch_pools_from_dashboard()
    if not pools:
        logger.error("❌ No pools found in dashboard")
        return {"error": "No pools found"}
    
    # Sort by importance
    if any('tvl' in p for p in pools):
        pools.sort(key=lambda x: x.get('tvl', 0), reverse=True)
    elif any('pool_votes_period' in p for p in pools):
        pools.sort(key=lambda x: x.get('pool_votes_period', 0), reverse=True)
    
    # Limit pools if specified
    if max_pools:
        pools = pools[:max_pools]
    
    num_updated = 0
    num_new = 0
    num_skipped = 0
    total_candles_added = 0
    
    logger.info(f"🔍 Fetching raw OHLCV data for {len(pools)} Shadow pools (DataFrame mode)...")
    logger.info(f"📁 Storage: {RAW_OHLCV_DIR}")
    logger.info(f"🔢 Initial fetch limit: {initial_hours} hours (API max: {MAX_API_LIMIT})\n")
    
    try:
        for i, pool in enumerate(pools):
            pool_addr = pool.get('pool', '').lower()
            pool_symbol = pool.get('symbol', 'Unknown')
            
            logger.info(f"[{i+1}/{len(pools)}] {pool_symbol} ({pool_addr[:10]}...)")
            
            # Rate limiting
            if i > 0:
                time.sleep(rate_limit_seconds)
            
            # Load existing data
            existing_df = None if force_update else load_pool_dataframe(pool_addr)
            
            if force_update or existing_df is None or len(existing_df) == 0:
                # Initial fetch: get full history (up to API limit)
                logger.info(f"   📊 Initial fetch: {initial_hours} hours")
                new_df = fetch_geckoterminal_ohlcv(pool_addr, limit_hours=initial_hours)
                num_new += 1
            else:
                # Incremental update: fetch only new data since last update
                last_timestamp = int(existing_df.index[0].timestamp())
                now_timestamp = int(datetime.datetime.now().timestamp())
                hours_elapsed = (now_timestamp - last_timestamp) // 3600
                
                if hours_elapsed < 1:
                    logger.info(f"   ⏭️  Skipping: updated <1 hour ago")
                    num_skipped += 1
                    continue
                
                # Fetch only new data (with small buffer for safety)
                # Cap at 1000 due to API limit
                fetch_hours = min(hours_elapsed + 2, MAX_API_LIMIT)
                logger.info(f"   🔄 Incremental: fetching {fetch_hours} hours (gap: {hours_elapsed}h)")
                new_df = fetch_geckoterminal_ohlcv(pool_addr, limit_hours=fetch_hours)
            
            if new_df is not None and len(new_df) > 0:
                # Merge with existing data
                merged_df = merge_ohlcv_dataframes(existing_df, new_df)
                
                # Calculate stats
                candles_before = len(existing_df) if existing_df is not None else 0
                candles_after = len(merged_df)
                candles_added = candles_after - candles_before
                total_candles_added += candles_added
                
                # Save DataFrame
                save_pool_dataframe(pool_addr, merged_df, symbol=pool_symbol)
                
                num_updated += 1
                
                # Log stats
                logger.info(f"   ✅ Saved: {candles_after:,} candles (+{candles_added})")
                logger.info(f"   📅 Range: {merged_df.index[-1]} → {merged_df.index[0]}")
            else:
                logger.warning(f"   ⚠️  Failed to fetch data")
    
    except KeyboardInterrupt:
        logger.warning("\n⚠️ Process interrupted by user!")
    except Exception as e:
        logger.error(f"\n❌ Error during fetch: {e}")
        import traceback
        traceback.print_exc()
    
    # Summary
    logger.info(f"\n{'='*60}")
    logger.info(f"✅ Fetch Complete")
    logger.info(f"{'='*60}")
    logger.info(f"📊 Pools processed:      {len(pools)}")
    logger.info(f"🔄 Pools updated:        {num_updated}")
    logger.info(f"🆕 New pools:            {num_new}")
    logger.info(f"⏭️  Pools skipped:        {num_skipped}")
    logger.info(f" Total candles added:  {total_candles_added:,}")
    logger.info(f" Data directory:       {RAW_OHLCV_DIR}")
    logger.info(f"{'='*60}\n")
    
    return {
        "pools_processed": len(pools),
        "pools_updated": num_updated,
        "pools_new": num_new,
        "pools_skipped": num_skipped,
        "total_candles_added": total_candles_added,
        "data_directory": RAW_OHLCV_DIR
    }

# ============================================================================
# Helper functions for using the raw OHLCV data
# ============================================================================

def load_all_pools():
    """Load all pool DataFrames
    
    Returns:
        dict: {pool_address: DataFrame} for all pools with data
    """
    pool_files = Path(RAW_OHLCV_DIR).glob("*.parquet")
    pools = {}
    
    for file in pool_files:
        pool_addr = file.stem  # filename without extension
        df = load_pool_dataframe(pool_addr)
        if df is not None:
            pools[pool_addr] = df
    
    logger.info(f"Loaded {len(pools)} pool DataFrames")
    return pools

def calculate_returns(df, periods=1):
    """Calculate returns from OHLCV DataFrame
    
    Args:
        df: OHLCV DataFrame
        periods: Number of periods for return calculation (default: 1 = hourly)
    
    Returns:
        pd.Series: Returns (r_t = close_t / close_(t-periods) - 1)
    """
    # Note: DataFrame is sorted newest first, so we use shift(-periods)
    returns = (df['close'] / df['close'].shift(-periods)) - 1
    return returns.dropna()

def calculate_volatility(df, window_hours=672, return_periods=1):
    """Calculate return volatility from OHLCV DataFrame
    
    Args:
        df: OHLCV DataFrame
        window_hours: Window size for volatility calculation (default: 672 = 28 days)
        return_periods: Periods for return calculation (default: 1 = hourly returns)
    
    Returns:
        float: Annualized volatility percentage
    """
    # Get most recent window_hours candles
    recent_df = df.head(window_hours)
    
    if len(recent_df) < 3:
        logger.warning("Not enough data for volatility calculation")
        return None
    
    # Calculate returns
    returns = calculate_returns(recent_df, periods=return_periods)
    
    if len(returns) < 2:
        logger.warning("Not enough returns for volatility calculation")
        return None
    
    # Calculate volatility (std dev of returns)
    vol = returns.std()
    
    # Return hourly volatility percentage (matching Aero optimizer logic)
    # Note: We do NOT annualize here because the optimizer expects raw hourly std dev
    return vol * 100

def calculate_covariance_matrix(pool_addresses, window_hours=672):
    """Calculate covariance matrix for multiple pools
    
    Args:
        pool_addresses: List of pool addresses
        window_hours: Window size for calculation (default: 672 = 28 days)
    
    Returns:
        pd.DataFrame: Covariance matrix
    """
    returns_dict = {}
    
    for addr in pool_addresses:
        df = load_pool_dataframe(addr)
        if df is not None and len(df) >= window_hours:
            recent_df = df.head(window_hours)
            returns = calculate_returns(recent_df)
            returns_dict[addr] = returns
    
    if not returns_dict:
        logger.error("No valid data for covariance calculation")
        return None
    
    # Create DataFrame of returns (aligned by timestamp)
    returns_df = pd.DataFrame(returns_dict)
    
    # Calculate covariance matrix
    cov_matrix = returns_df.cov()
    
    return cov_matrix

def calculate_correlation_matrix(pool_addresses, window_hours=672):
    """Calculate correlation matrix for multiple pools
    
    Args:
        pool_addresses: List of pool addresses
        window_hours: Window size for calculation (default: 672 = 28 days)
    
    Returns:
        pd.DataFrame: Correlation matrix
    """
    returns_dict = {}
    
    for addr in pool_addresses:
        df = load_pool_dataframe(addr)
        if df is not None and len(df) >= window_hours:
            recent_df = df.head(window_hours)
            returns = calculate_returns(recent_df)
            returns_dict[addr] = returns
    
    if not returns_dict:
        logger.error("No valid data for correlation calculation")
        return None
    
    # Create DataFrame of returns (aligned by timestamp)
    returns_df = pd.DataFrame(returns_dict)
    
    # Calculate correlation matrix
    corr_matrix = returns_df.corr()
    
    return corr_matrix

def get_volatility_map(window_hours=168):
    """Get volatility map for all pools
    
    Returns:
        dict: {pool_address: volatility_percent}
    """
    pools = load_all_pools()
    vol_map = {}
    
    for addr, df in pools.items():
        vol = calculate_volatility(df, window_hours=window_hours)
        if vol is not None:
            vol_map[addr.lower()] = vol
            
    return vol_map


if __name__ == "__main__":
    # Example: Fetch data for top 10 pools
    result = run_fetch_raw_volatility(max_pools=10)
    
    # Example: Load and analyze data
    print("\n" + "="*60)
    print("Example Analysis:")
    print("="*60)
    
    pools = load_all_pools()
    if pools:
        # Show volatilities
        print("\nPool Volatilities (28-day, annualized):")
        for addr, df in list(pools.items())[:5]:
            vol = calculate_volatility(df)
            if vol:
                print(f"  {addr[:10]}... : {vol:.2f}%")
