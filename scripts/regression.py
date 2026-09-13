"""Run a bounded regression loop; retain a log for each pass and stop on failure."""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--passes', type=int, default=3)
    parser.add_argument('--output', default='test-results')
    parser.add_argument('--browser', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.passes <= 10:
        parser.error('passes must be between 1 and 10')
    output = Path(args.output).resolve(); output.mkdir(parents=True, exist_ok=True)
    commands = [[sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
                ['node', '--test', 'tests/ui_flow.cjs']]
    if args.browser: commands.append(['node', '--test', 'tests/browser_flow.cjs'])
    runs = []
    for iteration in range(1, args.passes + 1):
        for index, cmd in enumerate(commands, 1):
            started = time.perf_counter()
            logfile = output / f'pass-{iteration}-suite-{index}.log'
            with logfile.open('w') as stream:
                proc = subprocess.run(cmd, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, timeout=300)
            result = {'pass': iteration, 'command': cmd, 'seconds': round(time.perf_counter()-started, 3), 'returncode': proc.returncode, 'log': logfile.name}
            runs.append(result); print(json.dumps(result), flush=True)
            (output / 'summary.json').write_text(json.dumps({'seed_range': [0, 19], 'csv_rows_per_python_pass': 1000, 'state_transitions_per_python_pass': 300, 'runs': runs}, indent=2))
            if proc.returncode: raise SystemExit(proc.returncode)
    print('All bounded regression passes completed. Model responses were test doubles; not live AI quality evidence.')

if __name__ == '__main__': main()
