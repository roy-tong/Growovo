#!/usr/bin/env python3
"""Run with the installed wheel's Python -I, never the source checkout."""
import importlib.metadata
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile

import core
from core import experience
from core.execution_policy import authorize
from core.grow_v2 import GrowRunV2
from core.targeting import TargetList


def main():
    checkout = Path(__file__).resolve().parents[1]
    assert checkout not in Path(core.__file__).resolve().parents, 'smoke imported source checkout'
    assert experience.load(), 'installed wheel lost reviewed experience records'
    import channels.x
    vendor = Path(channels.x.__file__).parent / 'vendor'
    assert (vendor / 'session_page.py').is_file()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        data = root / 'product data'
        facts = root / 'facts.json'
        facts.write_text(json.dumps({
            'product': {'name': 'Smoke', 'what': 'Invoice reconciliation for FinOps',
                        'stage': 'launched', 'product_type': 'devtool', 'url': 'https://example.invalid'},
            'icp': [{'hypothesis': 'FinOps teams', 'tags': ['developers'],
                     'evidence_label': 'hypothesis', 'evidence': 'synthetic smoke fixture'}],
            'value_events': [{'name': 'activation', 'source': 'fixture', 'is_approximation': False}],
            'constraints': {'red_lines': ['no bulk sends']},
            'baseline': {'followers': 10},
            'owned_assets': {'platforms': ['x']},
        }))
        env = dict(os.environ, MKT_HOME=str(data))
        for args in [('--help',), ('grow', 'platforms'),
                     ('target', 'start', 'dream'), ('target', 'add', 'dream',
                      '--platform', 'x', '--handle', 'peer', '--followers', '8000'),
                     ('target', 'next', 'dream')]:
            subprocess.run([sys.executable, '-I', '-m', 'mkt', *args], cwd=root,
                           env=env, check=True, stdout=subprocess.DEVNULL)
        authorize(data, by='offline smoke', actions=['x:reply'], days=1)
        run = GrowRunV2(data / 'grow' / 'smoke.json')
        GrowRunV2.launch(run.path, run_id='smoke', facts_path=str(facts),
            goal_id='awareness.followers', channels=['x'], actions=['x:reply'],
            actions_per_week=7, data_dir=data, skip_probe=True)
        assert not run.load().get('go'), 'launch must not arm live sends'
        installed = run.install(data, load=False)
        payload = plistlib.loads(installed['plist'].encode())
        assert payload['EnvironmentVariables']['MKT_HOME'] == str(data.resolve())
        assert len(TargetList(data / 'targets' / 'dream.json').next_batch()) == 1
    print('Installed wheel acceptance passed:', importlib.metadata.version('growovo'))


if __name__ == '__main__':
    main()
