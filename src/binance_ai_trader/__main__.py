import sys

if len(sys.argv) > 1 and sys.argv[1] == "live":
    from binance_ai_trader.v3.live.cli import main as live_main
    raise SystemExit(live_main())

from binance_ai_trader.entrypoints.cli import main

raise SystemExit(main())
