import copy
import json
from pathlib import Path
import tempfile
import unittest
from modelforge.config import demo_gateway
from modelforge.evaluation import evaluate, compare, lexical_f1, judge_answer
from modelforge.cli import ROOT, demo
from modelforge.types import GatewayError


class EvalTests(unittest.TestCase):
    def setUp(self):
        self.report = evaluate(demo_gateway(), ROOT / 'evals/dataset.jsonl')
        self.baseline = json.loads((ROOT / 'evals/baseline.json').read_text())

    def test_committed_baseline_passes(self):
        self.assertEqual(compare(self.report, self.baseline), [])

    def test_quality_cost_latency_regressions_block(self):
        for metric, value in [("pass_rate", .8), ("lexical_f1", .1), ("schema_compliance", 0), ("total_cost_usd", 2), ("p95_latency_ms", 9999)]:
            report = copy.deepcopy(self.report)
            report['metrics'][metric] = value
            self.assertTrue(compare(report, self.baseline), metric)

    def test_changed_dataset_and_missing_case_block(self):
        report = copy.deepcopy(self.report)
        report['dataset_sha256'] = 'different'
        report['cases'].pop()
        self.assertEqual(len(compare(report, self.baseline)), 2)

    def test_live_cannot_use_scripted_baseline(self):
        self.report['mode'] = 'live'
        self.assertTrue(compare(self.report, self.baseline))

    def test_nonfinite_metric_blocks(self):
        self.report['metrics']['lexical_f1'] = float('nan')
        self.assertTrue(compare(self.report, self.baseline))

    def test_lexical_proxy_is_not_semantic(self):
        self.assertEqual(lexical_f1('car', 'automobile'), 0)
        self.assertEqual(lexical_f1('a a', 'a'), 2/3)

    def test_demo_scenarios(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(demo(folder), 0)
            report = json.loads((Path(folder) / 'report.json').read_text())
            self.assertEqual(len(report['scenarios']), 7)
            self.assertTrue(report['scenarios'][1]['result']['cache_hit'])
            self.assertEqual(report['scenarios'][-1]['error'], 'no_route')
            self.assertIn('ModelForge', (Path(folder) / 'report.html').read_text())

    def test_judge_is_explicit_and_validated(self):
        class Judge:
            def complete(self, request):
                self.request = request
                return {'parsed': {'score': .9, 'reason': 'Correct'}, 'model': 'judge-model', 'accounted_cost_usd': .001}
        judge = Judge()
        result = judge_answer(judge, {'prompt': 'Question', 'expected': 'Reference'}, 'Candidate')
        self.assertEqual(result['score'], .9)
        self.assertFalse(judge.request.cache)
        self.assertIn('untrusted data', judge.request.prompt)
