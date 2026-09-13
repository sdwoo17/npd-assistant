"""Interactive local launcher; the Bedrock API key stays in this server process."""
import argparse
import getpass
import json
import os
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.model import BedrockModel
from app.store import AppError
from scripts.bootstrap_pilot import credentials_from_secret, initialize


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-id',required=True)
    parser.add_argument('--region',required=True)
    parser.add_argument('--profile',help='Existing AWS CLI/SSO profile; sign in to it first')
    parser.add_argument('--prompt-api-key',action='store_true',help='Read a Bedrock-only API key without displaying or saving it')
    parser.add_argument('--data-dir',default=str(ROOT/'runtime'))
    parser.add_argument('--assets',help='Private pack for a NEW data folder, optional')
    parser.add_argument('--publish-insights',action='store_true',help='Owner explicitly approves ALL insight drafts in the supplied pack')
    parser.add_argument('--port',type=int,default=8765)
    args=parser.parse_args()
    if args.profile and args.prompt_api_key:parser.error('Choose a profile or API-key prompt.')
    os.environ.update(NPD_MODEL_PROVIDER='bedrock',BEDROCK_MODEL_ID=args.model_id,AWS_REGION=args.region)
    if args.profile:os.environ['AWS_PROFILE']=args.profile
    if args.prompt_api_key:
        value=getpass.getpass('Bedrock API key (not saved): ').strip()
        if not value:parser.error('A Bedrock API key is required.')
        os.environ['AWS_BEARER_TOKEN_BEDROCK']=value
    try:
        BedrockModel().probe()
        print('Bedrock 실제 응답 확인. 고객 자료는 전송하지 않았습니다.')
        target=Path(args.data_dir)
        if not target.exists():
            values={}
            for role in ('owner','po'):
                values[role]={'email':input(role+' email: ').strip(),'password':getpass.getpass(role+' password (16+ characters): ')}
            initialize(target,credentials_from_secret(json.dumps(values)),args.assets,args.publish_insights)
        elif args.assets or args.publish_insights:
            raise AppError('자산 적재에는 새 데이터 폴더를 지정하세요. 기존 환경은 유지합니다.',409)
        import manage
        sys.argv=['manage.py','serve','--data-dir',str(target),'--port',str(args.port),'--require-model']
        manage.main()
        return 0
    except AppError as exc:
        print(str(exc),file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
