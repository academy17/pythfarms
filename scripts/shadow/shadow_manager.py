#!/usr/bin/env python3
import argparse
import logging
from lib import fetch_votes, optimizer, analytics, lp_optimizer

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
    optimize_parser.add_argument("--recompute", action="store_true", help="Recompute optimization with manually specified dashboard file")

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
            optimizer.run_optimize(args.period, save, True, args.recompute)
        else:
            optimizer.run_optimize(args.period, save, False, args.recompute)
    
    elif args.command == "analyze":
        logger.info(f"Analyzing performance for period {args.period if args.period else '[last voted]'}")
        analytics.run_analyze(args.period, args.compare)
    
    elif args.command == "lp_optimize":
        logger.info("Running LP optimization")
        lp_optimizer.run_lp_optimize(
            dashboard_path=args.dashboard,
            investment_amount=args.amount,
            save=args.save,
            display=args.display,
            top_n_pools=args.top
        )
    
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
