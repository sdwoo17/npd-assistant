import contextlib
import copy
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from botocore.exceptions import ClientError, NoCredentialsError, ReadTimeoutError
from app.model import BedrockModel, BedrockError, bedrock_error
from app.store import AppError, Store
from scripts.bedrock_check import check
from scripts.bootstrap_pilot import credentials_from_secret, initialize
from scripts.deploy_pilot import validate_parameters, require_new_stack
from scripts.live_validation import run_workflow
from tests.helpers import Fixture, RecordingModel, CANARY


class ProbeClient:
    def __init__(self):self.requests=[];self.error=None
    def converse(self,**request):
        self.requests.append(request)
        if self.error:raise self.error
        payload={'status':'ok'}
        mode='json_schema' if 'outputConfig' in request else 'tool'
        content=[{'text':json.dumps(payload)}] if mode=='json_schema' else [{'toolUse':{'name':'npd_probe','input':payload}}]
        return {'stopReason':'end_turn' if mode=='json_schema' else 'tool_use','output':{'message':{'content':content}},
            'ResponseMetadata':{'RequestId':'test-request-id'},'usage':{'inputTokens':10,'outputTokens':4}}


class BedrockRuntimeTests(unittest.TestCase):
    def test_probe_all_output_modes_excludes_private_data_and_records_verified_usage(self):
        for mode in ('json_schema','tool','strict_tool'):
            with self.subTest(mode=mode):
                client=ProbeClient();model=BedrockModel('test-model','ap-northeast-2',client,mode)
                self.assertFalse(model.status()['connection_verified'])
                result=model.probe()
                self.assertTrue(result['connection_verified']);self.assertEqual(result['last_call']['output_tokens'],4)
                self.assertNotIn(CANARY,json.dumps(client.requests))
                self.assertEqual(client.requests[0]['inferenceConfig']['maxTokens'],256)
                if mode=='strict_tool':self.assertTrue(client.requests[0]['toolConfig']['tools'][0]['toolSpec']['strict'])

    def test_real_sdk_serialization_contract_includes_structured_probe(self):
        import boto3
        from botocore.stub import Stubber
        for mode in ('json_schema','tool','strict_tool'):
            with self.subTest(mode=mode):
                client=boto3.client('bedrock-runtime',region_name='ap-northeast-2',aws_access_key_id='test',aws_secret_access_key='test')
                content=[{'text':'{"status":"ok"}'}] if mode=='json_schema' else [{'toolUse':{'toolUseId':'test-id','name':'npd_probe','input':{'status':'ok'}}}]
                with Stubber(client) as stub:
                    stub.add_response('converse',{'output':{'message':{'role':'assistant','content':content}},'stopReason':'end_turn' if mode=='json_schema' else 'tool_use','usage':{'inputTokens':1,'outputTokens':1,'totalTokens':2},'metrics':{'latencyMs':1}})
                    self.assertTrue(BedrockModel('test-model','ap-northeast-2',client,mode).probe()['connection_verified'])

    def test_provider_error_matrix_is_sanitized_and_clears_previous_success(self):
        codes=['AccessDeniedException','ValidationException','ExpiredTokenException','ResourceNotFoundException','ThrottlingException','ServiceQuotaExceededException','ModelTimeoutException','SomeUnexpectedCode']
        for code in codes:
            with self.subTest(code=code):
                client=ProbeClient();model=BedrockModel('test','ap-northeast-2',client)
                model.probe();client.error=ClientError({'Error':{'Code':code,'Message':CANARY+' SECRET_TOKEN'}},'Converse')
                with self.assertRaises(BedrockError) as error:model.probe()
                self.assertNotIn(CANARY,str(error.exception));self.assertNotIn('SECRET_TOKEN',json.dumps(model.status()))
                self.assertFalse(model.status()['connection_verified'])
        self.assertEqual(bedrock_error(NoCredentialsError()).code,'bedrock_credentials_missing')
        self.assertEqual(bedrock_error(ReadTimeoutError(endpoint_url='https://private.invalid')).status,504)

    def test_cli_never_reports_success_without_a_successful_live_response(self):
        model=BedrockModel(model='')
        result,code=check(model,False);self.assertEqual(code,2);self.assertFalse(result['live_call'])
        result,code=check(model,True);self.assertEqual(code,1);self.assertEqual(result['error_code'],'bedrock_not_configured')
        client=ProbeClient();model=BedrockModel('test','ap-northeast-2',client)
        result,code=check(model,True);self.assertEqual(code,0);self.assertTrue(result['connection_verified'])

    def test_probe_is_owner_only_throttled_and_never_sends_project_material(self):
        f=Fixture()
        try:
            client=ProbeClient();f.service.model=BedrockModel('test','ap-northeast-2',client)
            for route in ('/api/model/test','/api/model/status'):
                with self.assertRaises(AppError) as error:
                    f.service.post(f.po,route,{}) if route.endswith('test') else f.service.get(f.po,route)
                self.assertEqual(error.exception.status,403)
            self.assertEqual(client.requests,[])
            self.assertTrue(f.service.post(f.owner,'/api/model/test',{})['connection_verified'])
            with self.assertRaises(AppError) as error:f.service.post(f.owner,'/api/model/test',{})
            self.assertEqual(error.exception.status,429)
            self.assertNotIn(CANARY,json.dumps(client.requests))
            self.assertTrue(f.service.get(f.po,'/api/bootstrap')['model_connection_verified'])
            f.service._next_model_probe=0;client.error=NoCredentialsError()
            with self.assertRaises(AppError):f.service.post(f.owner,'/api/model/test',{})
            self.assertFalse(f.service.get(f.po,'/api/bootstrap')['model_connection_verified'])
        finally:f.close()

    def test_bearer_token_status_never_exposes_token(self):
        with patch.dict(os.environ,{'AWS_BEARER_TOKEN_BEDROCK':'SECRET_TEST_TOKEN'}):
            result=BedrockModel('test').status()
        self.assertEqual(result['authentication'],'bedrock_api_key')
        self.assertNotIn('SECRET_TEST_TOKEN',json.dumps(result))

    def test_live_http_gate_itself_with_explicit_test_double(self):
        # Tests the harness, not a live Bedrock claim. Runtime CLI creates BedrockModel only.
        model=RecordingModel();model.provider='bedrock'
        model.probe=lambda:{'connection_verified':True}
        result=run_workflow(model)
        self.assertEqual(result['status'],'PASS',result)
        self.assertEqual(result['persona_responses'],4)
        self.assertEqual(result['prd_version'],2)
        self.assertTrue(result['http_authentication'])


class PilotBootstrapTests(unittest.TestCase):
    def test_existing_stack_is_never_updated_and_auth_errors_are_not_missing_stacks(self):
        from unittest.mock import Mock
        client=Mock()
        with self.assertRaises(ValueError):require_new_stack(client,'existing-pilot')
        for code,message,expected in [('ValidationError','Stack with id test does not exist',None),('ValidationError','Invalid stack identifier',ClientError),('AccessDenied','Forbidden',ClientError)]:
            client.describe_stacks.side_effect=ClientError({'Error':{'Code':code,'Message':message}},'DescribeStacks')
            if expected:
                with self.assertRaises(expected):require_new_stack(client,'test')
            else:require_new_stack(client,'test')
        client.create_change_set.assert_not_called()

    def test_require_model_startup_rejects_public_passwords_and_failed_bedrock_before_binding(self):
        import manage
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'data';store=Store(target)
            store.create_user('owner@pilot.example','Owner-demo-2026!','owner','project')
            with patch('sys.argv',['manage.py','serve','--data-dir',str(target),'--require-model']),patch('manage.Server') as server,patch('manage.create_model') as factory:
                with self.assertRaisesRegex(SystemExit,'공개 샘플'):manage.main()
                factory.return_value.probe.assert_not_called();server.assert_not_called()
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'data';initialize(target,credentials_from_secret(json.dumps(self.values())))
            client=ProbeClient();client.error=NoCredentialsError()
            with patch('sys.argv',['manage.py','serve','--data-dir',str(target),'--require-model']),patch('manage.Server') as server,patch('manage.create_model',return_value=BedrockModel('test','ap-northeast-2',client)):
                with self.assertRaises(SystemExit):manage.main()
                server.assert_not_called()

    def values(self):
        return {'owner':{'email':'owner@pilot.example','password':'Owner-private-2026-X!'},'po':{'email':'po@pilot.example','password':'Planner-private-2026-X!'}}

    def test_secret_validation_loop_never_echoes_values(self):
        mutations=[lambda x:x.pop('owner'),lambda x:x['po'].update(email=x['owner']['email']),lambda x:x['owner'].update(password='Owner-demo-2026!'),lambda x:x['po'].update(password=123),lambda x:x['po'].update(email='invalid')]
        for mutate in mutations:
            value=self.values();mutate(value)
            with self.assertRaises(AppError) as error:credentials_from_secret(json.dumps(value))
            self.assertNotIn('private-2026',str(error.exception))

    def test_non_demo_credentials_bootstrap_is_atomic_and_does_not_log_passwords(self):
        credentials=credentials_from_secret(json.dumps(self.values()))
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'data';out=io.StringIO()
            with contextlib.redirect_stdout(out):initialize(target,credentials)
            self.assertNotIn('private-2026',out.getvalue());store=Store(target)
            self.assertFalse(store.has_public_demo_passwords())
            for role,(email,password) in credentials.items():
                token,_=store.login(email,password);self.assertEqual(store.authenticate(token)['role'],role)
            before=store.path.read_bytes()
            with self.assertRaises(AppError):initialize(target,credentials)
            self.assertEqual(before,store.path.read_bytes())
            self.assertEqual(list(Path(tmp).glob('pilot-staging-*')),[])

    def test_public_password_detection_cannot_be_bypassed_by_renaming_email(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(tmp);store.create_user('custom-name@pilot.example','Owner-demo-2026!','owner','p')
            self.assertTrue(store.has_public_demo_passwords())

    def test_deployment_parameters_reject_public_network_and_shell_injection(self):
        template=json.loads((Path(__file__).resolve().parents[1]/'infra/pilot.json').read_text())
        values={'VpcId':'vpc-123abc','PublicSubnetIds':'subnet-123,subnet-456','DomainName':'npd.example.test','CustomerCidr':'203.0.113.10/32','CertificateArn':'arn:aws:acm:ap-northeast-2:123456789012:certificate/abc','BootstrapSecretArn':'arn:aws:secretsmanager:ap-northeast-2:123456789012:secret:npd-abc','BedrockModelId':'test-model','BedrockResourceArns':'arn:aws:bedrock:ap-northeast-2::foundation-model/test','CodeCommit':'a'*40}
        self.assertTrue(validate_parameters(values,template))
        for key,value in [('CustomerCidr','0.0.0.0/0'),('CustomerCidr','999.1.1.1/32'),('DomainName',"npd.example'; echo secret"),('BedrockModelId','$(echo secret)'),('CodeCommit','main'),('PublicSubnetIds','subnet-123,subnet-123'),('BootstrapSecretArn','bad\nsecret')]:
            with self.subTest(key=key),self.assertRaises(ValueError):validate_parameters({**values,key:value},template)
