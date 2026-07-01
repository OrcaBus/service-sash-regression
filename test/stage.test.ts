import { App, Aspects, Stack } from 'aws-cdk-lib';
import { Annotations, Match } from 'aws-cdk-lib/assertions';
import { SynthesisMessage } from 'aws-cdk-lib/cx-api';
import { AwsSolutionsChecks, NagSuppressions } from 'cdk-nag';
import { SashRegressionStack } from '../infrastructure/stage/deployment-stack';
import { getStackProps } from '../infrastructure/stage/config';

function synthesisMessageToString(sm: SynthesisMessage): string {
  return `${sm.entry.data} [${sm.id}]`;
}

describe('cdk-nag-sash-regression-stack', () => {
  const app = new App({});

  const deployStack = new SashRegressionStack(app, 'SashRegressionStack', {
    ...getStackProps('BETA'),
    env: {
      account: '111111111111',
      region: 'ap-southeast-2',
    },
  });

  Aspects.of(deployStack).add(new AwsSolutionsChecks());
  applyNagSuppression(deployStack);

  test('cdk-nag AwsSolutions Pack errors', () => {
    const errors = Annotations.fromStack(deployStack)
      .findError('*', Match.stringLikeRegexp('AwsSolutions-.*'))
      .map(synthesisMessageToString);
    expect(errors).toHaveLength(0);
  });

  test('cdk-nag AwsSolutions Pack warnings', () => {
    const warnings = Annotations.fromStack(deployStack)
      .findWarning('*', Match.stringLikeRegexp('AwsSolutions-.*'))
      .map(synthesisMessageToString);
    expect(warnings).toHaveLength(0);
  });
});

function applyNagSuppression(stack: Stack) {
  NagSuppressions.addStackSuppressions(
    stack,
    [
      {
        id: 'AwsSolutions-L1',
        reason: 'Python 3.12 is the current platform standard runtime version',
      },
      {
        id: 'AwsSolutions-IAM4',
        reason: 'AWSLambdaBasicExecutionRole is required for Lambda CloudWatch logging',
      },
      {
        id: 'AwsSolutions-IAM5',
        reason: 'Lambda log group name is not known at synth time; wildcard is required',
      },
      {
        id: 'AwsSolutions-APIG1',
        reason:
          'SubmitterApi is an internal manual-trigger tool; Lambda CloudWatch logs are sufficient',
      },
      {
        id: 'AwsSolutions-APIG2',
        reason:
          'SubmitterApi is an internal manual-trigger tool; input validation is handled in the Lambda handler',
      },
      {
        id: 'AwsSolutions-APIG4',
        reason:
          'SubmitterApi is an internal tool invoked by authorised operators; no public auth required',
      },
      {
        id: 'AwsSolutions-APIG6',
        reason:
          'SubmitterApi is an internal manual-trigger tool; Lambda CloudWatch logs are sufficient',
      },
      {
        id: 'AwsSolutions-APIG3',
        reason: 'SubmitterApi is an internal manual-trigger tool; WAF web ACL is not required',
      },
      {
        id: 'AwsSolutions-COG4',
        reason: 'SubmitterApi is an internal tool; Cognito user pool authorizer is not required',
      },
    ],
    true
  );
}
