"""Measure caller-supplied relevance labels without logging research or questions."""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.model import create_model
from app.operations import existing_store
from app.service import Service
from app.store import AppError


def evaluate(service, project, cases, receipt=None, passes=3, label_origin='authored'):
    if not isinstance(cases, list) or not 1 <= len(cases) <= 200 or not 1 <= passes <= 10:
        raise AppError('질문 1~200개, 반복 1~10회가 필요합니다.')
    insight_ids = (receipt or {}).get('insight_ids', {})
    results = []
    for i, case in enumerate(cases):
        question = case.get('query', case.get('question'))
        expected = case.get('expected_evidence_ids')
        if expected is None:
            keys = case.get('expected_insight_keys', case.get('insight_keys', []))
            if not isinstance(keys, list) or not keys or any(k not in insight_ids for k in keys):
                raise AppError('기대 근거 ID 또는 적재 영수증과 일치하는 인사이트 키가 필요합니다.')
            expected = [insight_ids[k] for k in keys]
        if (not isinstance(question, str) or not 1 <= len(question.strip()) <= 5000
                or not isinstance(expected, list) or not expected or any(not isinstance(e, str) for e in expected)):
            raise AppError('질문 또는 기대 근거 형식을 확인하세요.')
        ranks, times, ids_seen = [], [], []
        for _ in range(passes):
            start = time.perf_counter()
            rows, _ = service.search(project, question, case.get('filters'))
            times.append((time.perf_counter() - start) * 1000)
            ids = [r['id'] for r in rows]
            ranks.append(next((n + 1 for n, eid in enumerate(ids) if eid in expected), None))
            ids_seen.append(ids)
        results.append({'case_number': i + 1, 'first_relevant_ranks': ranks,
            'stable_across_passes': all(ids == ids_seen[0] for ids in ids_seen),
            'median_ms': round(statistics.median(times), 3), 'max_ms': round(max(times), 3)})
    ranks = [r for item in results for r in item['first_relevant_ranks']]
    return {'schema': 'npd.retrieval-evaluation.v1', 'cases': len(cases), 'passes': passes,
            'label_origin_declared': label_origin, 'model_calls': 0,
            'hit_rate_at_24': round(sum(r is not None for r in ranks) / len(ranks), 4),
            'mean_reciprocal_rank_at_24': round(sum(1 / r for r in ranks if r is not None) / len(ranks), 4),
            'all_ranks_stable': all(r['stable_across_passes'] for r in results), 'results': results,
            'limitations': 'Candidate retrieval only. Authored labels are not independent gold; no live AI or customer usefulness claim.'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-dir', required=True); p.add_argument('--project-id', required=True)
    p.add_argument('--cases', required=True); p.add_argument('--receipt')
    p.add_argument('--passes', type=int, choices=range(1, 11), default=3)
    p.add_argument('--label-origin', choices=('authored', 'independent'), default='authored')
    p.add_argument('--output'); p.add_argument('--minimum-hit-rate', type=float)
    args = p.parse_args()
    if args.minimum_hit_rate is not None and not 0 <= args.minimum_hit_rate <= 1:
        p.error('minimum-hit-rate must be between 0 and 1')
    try:
        store = existing_store(args.data_dir)
        receipt = json.loads(Path(args.receipt).read_text()) if args.receipt else None
        result = evaluate(Service(store, create_model()), args.project_id, json.loads(Path(args.cases).read_text()), receipt, args.passes, args.label_origin)
    except (AppError, ValueError, TypeError, KeyError, OSError):
        print('평가 입력·DB·근거 참조를 확인하세요. 입력 원문은 출력하지 않습니다.', file=sys.stderr)
        return 1
    output = json.dumps(result, ensure_ascii=False, indent=2)
    print(output)
    if args.output:
        Path(args.output).write_text(output + '\n')
    return 1 if args.minimum_hit_rate is not None and result['hit_rate_at_24'] < args.minimum_hit_rate else 0


if __name__ == '__main__':
    raise SystemExit(main())
