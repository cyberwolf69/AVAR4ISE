"""Single command entry point for local development and demo operation."""
import argparse
import importlib
import runpy
import sys

COMMANDS = {
    'paper-demo': 'paper_demo', 'risk-demo': 'demo', 'scan': 'scan',
    'research': 'research', 'preflight': 'okx_demo', 'agent': 'demo_agent',
    'run': 'portfolio_runner', 'bounded-run': 'demo_runner', 'reconcile': 'reconcile_demo',
    'execution-test': 'demo_execution', 'paper-bridge': 'paper_bridge',
    'ai-check': 'ai_client', 'evaluate-ai': 'evaluate_ai',
    'reversion': 'reversion_research',
    'alerts': 'alerts', 'universe': 'universe', 'signals': 'signal_history', 'improve': 'improve', 'shadow': 'shadow',
}


def main():
    parser = argparse.ArgumentParser(description='OKX Demo trading and research tools')
    parser.add_argument('command', choices=COMMANDS)
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    module = 'hedging_tools.' + COMMANDS[args.command]
    sys.argv = [f'hedging-tools {args.command}', *args.arguments]
    if args.command == 'risk-demo':
        runpy.run_module(module, run_name='__main__')
        return 0
    return importlib.import_module(module).main()


if __name__ == '__main__':
    raise SystemExit(main())
