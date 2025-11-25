#!/usr/bin/env python3
import argparse
import logging
import os
from dotenv import load_dotenv
from lib import fetch_votes, optimizer, analytics, lp_optimizer_old, fetch_lp_data, fetch_volatility, fetch_raw_volatility

# Load environment variables from .env file
load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def main():
    parser = argparse.ArgumentParser(description="Shadow Farm Vote Manager")
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Fetch subcommand
    fetch_parser = subparsers.add_parser("fetch", help="Fetch votes and rewards data")
    fetch_parser.add_argument("--type", choices=["votes", "rewards", "all"], default="all", help="Type of data to fetch")
    fetch_parser.add_argument("--period", type=int, help="Period to fetch (default: current period)")
    fetch_parser.add_argument("--historical_dashboard_path", type=str, help="Path to existing dashboard for historical fetch")
    fetch_parser.add_argument("--with-volatility", action="store_true", help="Include volatility data (slower but more comprehensive)")

    # Optimize subcommand
    optimize_parser = subparsers.add_parser("optimize", help="Run vote optimizer")
    optimize_parser.add_argument("--period", type=int, help="Period to optimize (default: next period)")
    optimize_parser.add_argument("--save", action="store_true", help="Save results to file")
    optimize_parser.add_argument("--historical", action="store_true", help="Run historical optimization")
    optimize_parser.add_argument("--display", action="store_true", help="Display results without saving")
    optimize_parser.add_argument("--recompute", action="store_true", help="Recompute optimization with manually specified dashboard file")
    optimize_parser.add_argument("--with-volatility", action="store_true", help="Apply volatility penalty to volatile pools")
    optimize_parser.add_argument("--gamma", type=float, default=1.0, help="Volatility penalty coefficient (default 1.0, higher = stronger penalty)")

    # Analyze subcommand
    analyze_parser = subparsers.add_parser("analyze", help="Analyze vote performance")
    analyze_parser.add_argument("--period", type=int, help="Period to analyze (default: last voted period)")
    analyze_parser.add_argument("--compare", action="store_true", help="Compare with optimal allocation")

    # LP Optimize subcommand
    lp_optimize_parser = subparsers.add_parser("lp_optimize", help="Optimize LP allocations based on APR and TVL")
    lp_optimize_parser.add_argument("--dashboard", type=str, help="Path to votes dashboard file")
    lp_optimize_parser.add_argument("--amount", type=float, help="Investment amount in USD")
    lp_optimize_parser.add_argument("--save", action="store_true", default=True, help="Save results to file")
    lp_optimize_parser.add_argument("--display", action="store_true", default=True, help="Display results")
    lp_optimize_parser.add_argument("--top", type=int, help="Only consider top N pools by TVL")

    # LP Dashboard subcommand
    lp_dashboard_parser = subparsers.add_parser("lp_dashboard", help="Generate APR dashboard for LP positions at different investment sizes")
    lp_dashboard_parser.add_argument("--sizes", type=float, nargs="+", help="Investment sizes to calculate APR for (default: 1000, 10000, 50000)")
    lp_dashboard_parser.add_argument("--top", type=int, default=30, help="Number of top pools to display (default: 30)")
    lp_dashboard_parser.add_argument("--no-save", action="store_true", help="Don't save dashboard to file")
    lp_dashboard_parser.add_argument("--no-display", action="store_true", help="Don't display dashboard")

    # Fetch Volatility subcommand
    fetch_volatility_parser = subparsers.add_parser("fetch_volatility", help="Fetch volatility data for Shadow pools from GeckoTerminal")
    fetch_volatility_parser.add_argument("--max-pools", type=int, default=500, help="Maximum number of pools to process (default: 500)")
    fetch_volatility_parser.add_argument("--rate-limit", type=int, default=2, help="Seconds between API calls (default: 2)")
    fetch_volatility_parser.add_argument("--force-update", action="store_true", help="Force update all pools regardless of cache")
    fetch_volatility_parser.add_argument("--month", action="store_true", help="Fetch 30-day (720 hour) volatility instead of 7-day (168 hour)")

    # Fetch Raw Volatility (Parquet/Incremental) subcommand
    fetch_raw_volatility_parser = subparsers.add_parser("fetch_raw_volatility", help="Fetch raw OHLCV data incrementally (Parquet)")
    fetch_raw_volatility_parser.add_argument("--max", type=int, help="Maximum number of pools to process")
    fetch_raw_volatility_parser.add_argument("--force", action="store_true", help="Force update all pools regardless of cache")
    fetch_raw_volatility_parser.add_argument("--initial-hours", type=int, default=1000, help="Number of hours to fetch on first run")

    args = parser.parse_args()

    if args.command == "fetch":
        logger.info(f"Fetching data for period {args.period if args.period else '[current]'}")
        if args.with_volatility:
            logger.info("Including volatility data collection (may take longer)")
        fetch_votes.run_fetch(period=args.period, historical_dashboard_path=args.historical_dashboard_path, with_volatility=args.with_volatility)
    elif args.command == "optimize":
        save = not args.display
        logger.info(f"Optimizing votes for period {args.period if args.period else '[next/historical]'}")
        
        if args.historical:
            logger.info("Running historical optimization")
            optimizer.run_optimize(args.period, save, True, args.recompute, 
                                 with_volatility=args.with_volatility, gamma=args.gamma)
        else:
            optimizer.run_optimize(args.period, save, False, args.recompute,
                                 with_volatility=args.with_volatility, gamma=args.gamma)
    
    elif args.command == "analyze":
        logger.info(f"Analyzing performance for period {args.period if args.period else '[last voted]'}")
        analytics.run_analyze(args.period, args.compare)
    
    elif args.command == "lp_optimize":
        logger.info("Running LP optimization")
        lp_optimizer_old.run_lp_optimize(
            dashboard_path=args.dashboard,
            investment_amount=args.amount,
            save=args.save,
            display=args.display,
            top_n_pools=args.top
        )
    
    elif args.command == "lp_dashboard":
        logger.info("Generating LP APR dashboard")
        investment_sizes = args.sizes if args.sizes else None
        fetch_lp_data.run_fetch_lp_data(
            investment_sizes=investment_sizes,
            display=not args.no_display,
            save=not args.no_save,
            top_n=args.top
        )
    
    elif args.command == "fetch_volatility":
        logger.info("Fetching volatility data for Shadow pools")
        timeframe = "30-day" if args.month else "7-day"
        logger.info(f"Using {timeframe} volatility calculation")
        fetch_volatility.run_fetch_volatility(
            max_pools=args.max_pools,
            rate_limit_seconds=args.rate_limit,
            force_update=args.force_update,
            use_monthly=args.month
        )

    elif args.command == "fetch_raw_volatility":
        logger.info("Fetching raw OHLCV data (Incremental/Parquet)")
        fetch_raw_volatility.run_fetch_raw_volatility(
            max_pools=args.max,
            force_update=args.force,
            initial_hours=args.initial_hours
        )
    
    else:
        parser.print_help()

if __name__ == "__main__":
    main()