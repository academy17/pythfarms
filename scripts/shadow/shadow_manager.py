#!/usr/bin/env python3
import argparse
import logging
from lib import fetch_votes, optimizer, analytics

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
    fetch_parser.add_argument("--skip-volatility", action="store_true", help="Skip fetching volatility data to speed up the process")

    # Optimize subcommand
    optimize_parser = subparsers.add_parser("optimize", help="Run vote optimizer")
    optimize_parser.add_argument("--period", type=int, help="Period to optimize (default: next period)")
    optimize_parser.add_argument("--save", action="store_true", help="Save results to file")
    optimize_parser.add_argument("--historical", action="store_true", help="Run historical optimization")
    optimize_parser.add_argument("--display", action="store_true", help="Display results without saving")

    # Analyze subcommand
    analyze_parser = subparsers.add_parser("analyze", help="Analyze vote performance")
    analyze_parser.add_argument("--period", type=int, help="Period to analyze (default: last voted period)")
    analyze_parser.add_argument("--compare", action="store_true", help="Compare with optimal allocation")

    args = parser.parse_args()

    if args.command == "fetch":
        logger.info(f"Fetching data for period {args.period if args.period else '[current]'}")
        if args.skip_volatility:
            logger.info("Skipping volatility data collection")
        fetch_votes.run_fetch(period=args.period, historical_dashboard_path=args.historical_dashboard_path, skip_volatility=args.skip_volatility)
    elif args.command == "optimize":
        save = not args.display
        logger.info(f"Optimizing votes for period {args.period if args.period else '[next/historical]'}")
        
        if args.historical:
            logger.info("Running historical optimization")
            optimizer.run_optimize(args.period, save, True)
        else:
            optimizer.run_optimize(args.period, save, False)
    
    elif args.command == "analyze":
        logger.info(f"Analyzing performance for period {args.period if args.period else '[last voted]'}")
        analytics.run_analyze(args.period, args.compare)
    
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
