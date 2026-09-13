"""Measure a caller-supplied CSV without copying its text into reports or the repo."""
import argparse
import base64
import csv
import io
import json
import statistics
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.model import create_model
from app.service import Service
from app.store import Store


def main():
    p=argparse.ArgumentParser();p.add_argument('--csv',required=True);p.add_argument('--output');args=p.parse_args()
    original=Path(args.csv).read_text(encoding='utf-8-sig')
    rows=list(csv.DictReader(io.StringIO(original)))
    # Blank supplied labels so retrieval benchmarks do not quietly use ground truth.
    buf=io.StringIO();w=csv.DictWriter(buf,fieldnames=list(rows[0]) if rows else ['external_id','text']);w.writeheader()
    for row in rows:w.writerow({**row,'feature':''} if 'feature' in row else row)
    with tempfile.TemporaryDirectory() as tmp:
        service=Service(Store(tmp),create_model());owner={'id':'benchmark','role':'owner','project_id':'benchmark'}
        body={'filename':'input.csv','content_base64':base64.b64encode(buf.getvalue().encode()).decode(),'source_name':'private-benchmark'}
        start=time.perf_counter();r=service.voc_upload(owner,body);import_ms=(time.perf_counter()-start)*1000
        again=service.voc_upload(owner,body)
        actual=service.voc_records('benchmark');primary=Counter(x['feature'] for x in actual)
        queries=['소재 A/B 테스트 비교 조건','추천 소재의 근거와 판단 보류','소규모 광고주의 성과 리포트','대행사 고객 승인과 공유']
        measurements=[]
        for q in queries:
            elapsed=[]
            for _ in range(20):
                start=time.perf_counter();matches,meta=service.search('benchmark',q);elapsed.append((time.perf_counter()-start)*1000)
            measurements.append({'query':q,'returned':len(matches),'median_ms':round(statistics.median(elapsed),3),'p95_ms':round(sorted(elapsed)[18],3)})
        result={'input_rows':len(rows),'imported':r['imported'],'duplicates':r['duplicates'],'errors':len(r['errors']),'import_ms':round(import_ms,3),
                'repeat_imported':again['imported'],'repeat_duplicates':again['duplicates'],'evidence_types':dict(Counter(x['evidence_type'] for x in actual)),
                'primary_features':dict(primary),'unclassified_ratio':round(primary['unclassified']/max(1,len(actual)),4),
                'provided_feature_labels':sum(bool(r.get('feature')) for r in rows),'precision_recall':'not evaluated; requires independently adjudicated gold labels',
                'model_calls':0,'search':measurements,'notes':'Weighted lexical retrieval, no live LLM or semantic-relevance grading. Input text and external IDs omitted.'}
    output=json.dumps(result,ensure_ascii=False,indent=2);print(output)
    if args.output:Path(args.output).write_text(output+'\n')

if __name__=='__main__':main()
