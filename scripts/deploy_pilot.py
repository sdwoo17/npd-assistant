"""Validate deployment inputs, then optionally create and execute an AWS change set."""
import argparse
import ipaddress
import json
import re
import subprocess
import sys
import time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]


def require_new_stack(client, stack_name):
    from botocore.exceptions import ClientError
    try:
        client.describe_stacks(StackName=stack_name)
    except ClientError as exc:
        error = exc.response.get('Error', {})
        if error.get('Code') == 'ValidationError' and 'does not exist' in error.get('Message', ''):
            return
        raise
    # EC2 user-data does not reliably re-run during stack updates. Never imply
    # that changing CodeCommit has deployed new code to an existing instance.
    raise ValueError('This command creates a NEW pilot only. Existing stacks require an explicit release and verified backup/restore procedure; nothing was changed.')


def validate_parameters(values, template):
    if not isinstance(values, dict):
        raise ValueError('Parameters must be a JSON object.')
    definitions = template['Parameters']
    if set(values) - set(definitions):
        raise ValueError('Unknown CloudFormation parameter.')
    for key, spec in definitions.items():
        value = values.get(key, spec.get('Default'))
        if not isinstance(value, str) or not value:
            raise ValueError('Missing parameter: ' + key)
        if 'AllowedValues' in spec and value not in spec['AllowedValues']:
            raise ValueError('Invalid value: ' + key)
        candidates = value.split(',') if spec['Type'] == 'CommaDelimitedList' else [value]
        if 'AllowedPattern' in spec and any(not re.fullmatch(spec['AllowedPattern'], item.strip()) for item in candidates):
            raise ValueError('Invalid format: ' + key)
    network = ipaddress.ip_network(values['CustomerCidr'], strict=True)
    if network.version != 4 or network.prefixlen == 0:
        raise ValueError('Use a restricted office/VPN IPv4 CIDR, not public 0.0.0.0/0.')
    subnets = [s.strip() for s in values['PublicSubnetIds'].split(',')]
    if len(set(subnets)) < 2 or any(not re.fullmatch(r'subnet-[a-f0-9]+', s) for s in subnets):
        raise ValueError('At least two distinct public subnet IDs are required.')
    return [{'ParameterKey': k, 'ParameterValue': v} for k, v in values.items()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parameters', required=True, help='Private JSON with actual environment IDs; no passwords')
    parser.add_argument('--stack-name', default='npd-assistant-pilot')
    parser.add_argument('--region', required=True)
    parser.add_argument('--execute', action='store_true', help='Create paid resources in the authenticated AWS account')
    args = parser.parse_args()
    try:
        template = json.loads((ROOT/'infra/pilot.json').read_text())
        values = json.loads(Path(args.parameters).read_text())
        values.setdefault('CodeCommit', subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip())
        parameters = validate_parameters(values, template)
        if not args.execute:
            print(json.dumps({'status':'VALIDATED_LOCALLY','resource_count':len(template['Resources']),
                'commit':values['CodeCommit'],'aws_called':False,'next':'Run with --execute after configuring AWS credentials and verifying the supplied environment.'}))
            return 0
        if subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip():
            raise ValueError('Commit and publish working-tree changes before deployment.')
        import boto3
        client = boto3.client('cloudformation',region_name=args.region)
        require_new_stack(client, args.stack_name)
        name='npd-'+str(int(time.time()))
        result=client.create_change_set(StackName=args.stack_name,ChangeSetName=name,
            ChangeSetType='CREATE',TemplateBody=json.dumps(template),Parameters=parameters,Capabilities=['CAPABILITY_IAM'])
        client.get_waiter('change_set_create_complete').wait(ChangeSetName=result['Id'])
        change=client.describe_change_set(ChangeSetName=result['Id'])
        # Instance replacement loses the active mount; requires a separate backup/restore plan.
        if any(x['ResourceChange'].get('LogicalResourceId')=='Host' and x['ResourceChange'].get('Replacement') in ('True','Conditional') for x in change.get('Changes',[])):
            raise ValueError('Change set retained without execution: host replacement requires verified backup and restore first.')
        client.execute_change_set(ChangeSetName=result['Id'])
        print(json.dumps({'status':'DEPLOYMENT_STARTED','stack_id':result['StackId'],
            'next':'Wait for CREATE_COMPLETE, then set the DNS CNAME and run the live workflow gate. This is not a customer-ready verdict.'}))
        return 0
    except ValueError as exc:
        print(str(exc),file=sys.stderr)
    except Exception:
        print('Deployment could not start. Check AWS authentication, CloudFormation events, IAM and parameters. No secret values are logged.',file=sys.stderr)
    return 1


if __name__ == '__main__': raise SystemExit(main())
