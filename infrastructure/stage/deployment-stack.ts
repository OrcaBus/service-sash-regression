import * as path from 'path';
import { Construct } from 'constructs';
import { DockerImageCode, DockerImageFunction, Function, IFunction } from 'aws-cdk-lib/aws-lambda';
import { aws_lambda, CfnOutput, Duration, Size, Stack, StackProps } from 'aws-cdk-lib';
import { LambdaRestApi } from 'aws-cdk-lib/aws-apigateway';
import { EventBus, IEventBus, Rule } from 'aws-cdk-lib/aws-events';
import { LambdaFunction } from 'aws-cdk-lib/aws-events-targets';
import { ManagedPolicy, PolicyStatement, Role, ServicePrincipal } from 'aws-cdk-lib/aws-iam';
import {
  APP_ROOT,
  TESTDATA_BUCKET,
  RESULTS_BUCKET,
  ORCABUS_TOKEN_SECRET_ID,
  HOSTNAME_SSM_PARAMETER_NAME,
  EVENT_BUS_NAME,
  TESTDATA_TUMOR_LIBRARY_ID,
  TESTDATA_NORMAL_LIBRARY_ID,
  getStageConstants,
} from './constants';
import { StageName } from '@orcabus/platform-cdk-constructs/shared-config/accounts';

export interface SashRegressionStackProps extends StackProps {
  stage: string;
}

export class SashRegressionStack extends Stack {
  constructor(scope: Construct, id: string, props: SashRegressionStackProps) {
    super(scope, id, props);

    const { testdataConfigS3Uri, resultS3Prefix, wruDraftValidatorFunctionName } =
      getStageConstants(props.stage as StageName);

    const mainBus = EventBus.fromEventBusName(this, 'OrcaBusMain', EVENT_BUS_NAME);

    // Each Lambda gets its own execution role. A single role shared across all three,
    // with a policy statement referencing one function's ARN, creates a circular
    // CloudFormation dependency once another function using that same role exists.
    const comparatorFn = this.createComparatorFunction(testdataConfigS3Uri, resultS3Prefix);
    this.createSubmitterFunction(wruDraftValidatorFunctionName, mainBus);
    this.createWatcherFunction(comparatorFn, mainBus);
  }

  private createComparatorFunction(testdataConfigS3Uri: string, resultS3Prefix: string): IFunction {
    const role = new Role(this, 'ComparatorRole', {
      assumedBy: new ServicePrincipal('lambda.amazonaws.com'),
      description: 'Lambda execution role for SashRegression Comparator',
    });
    role.addManagedPolicy(
      ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole')
    );

    // Read sash outputs from pipeline cache buckets and project-data buckets (e.g. project-wgs-accreditation)
    role.addToPolicy(
      new PolicyStatement({
        actions: ['s3:GetObject', 's3:ListBucket'],
        resources: [
          'arn:aws:s3:::pipeline-*-cache-*',
          'arn:aws:s3:::pipeline-*-cache-*/*',
          'arn:aws:s3:::project-data-*',
          'arn:aws:s3:::project-data-*/*',
        ],
      })
    );

    // Read baseline config from testdata bucket (read-only — never write here)
    role.addToPolicy(
      new PolicyStatement({
        actions: ['s3:GetObject', 's3:ListBucket'],
        resources: [`arn:aws:s3:::${TESTDATA_BUCKET}`, `arn:aws:s3:::${TESTDATA_BUCKET}/*`],
      })
    );

    // Read config and write results to umccr-research-dev (all stages)
    role.addToPolicy(
      new PolicyStatement({
        actions: ['s3:GetObject', 's3:PutObject', 's3:ListBucket'],
        resources: [`arn:aws:s3:::${RESULTS_BUCKET}`, `arn:aws:s3:::${RESULTS_BUCKET}/*`],
      })
    );

    return new DockerImageFunction(this, 'ComparatorFunction', {
      code: DockerImageCode.fromImageAsset(path.join(APP_ROOT)),
      architecture: aws_lambda.Architecture.ARM_64,
      timeout: Duration.minutes(15),
      memorySize: 4096,
      ephemeralStorageSize: Size.gibibytes(10),
      role,
      environment: {
        TESTDATA_CONFIG_S3_URI: testdataConfigS3Uri,
        RESULT_S3_PREFIX: resultS3Prefix,
      },
    });
  }

  private createSubmitterFunction(wruDraftValidatorFunctionName: string, mainBus: IEventBus): void {
    const role = new Role(this, 'SubmitterRole', {
      assumedBy: new ServicePrincipal('lambda.amazonaws.com'),
      description: 'Lambda execution role for SashRegression Submitter',
    });
    role.addManagedPolicy(
      ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole')
    );

    // Read OrcaBus token from Secrets Manager
    role.addToPolicy(
      new PolicyStatement({
        actions: ['secretsmanager:GetSecretValue'],
        resources: [
          `arn:aws:secretsmanager:${this.region}:${this.account}:secret:${ORCABUS_TOKEN_SECRET_ID}*`,
        ],
      })
    );

    // Read hostname from SSM
    role.addToPolicy(
      new PolicyStatement({
        actions: ['ssm:GetParameter'],
        resources: [
          `arn:aws:ssm:${this.region}:${this.account}:parameter${HOSTNAME_SSM_PARAMETER_NAME}`,
        ],
      })
    );

    // Invoke WruDraftValidator
    const wruFn = Function.fromFunctionName(
      this,
      'WruDraftValidatorFn',
      wruDraftValidatorFunctionName
    );
    role.addToPolicy(
      new PolicyStatement({
        actions: ['lambda:InvokeFunction'],
        resources: [wruFn.functionArn],
      })
    );

    // Emit events to OrcaBusMain
    role.addToPolicy(
      new PolicyStatement({
        actions: ['events:PutEvents'],
        resources: [mainBus.eventBusArn],
      })
    );

    const submitterFn = new DockerImageFunction(this, 'SubmitterFunction', {
      code: DockerImageCode.fromImageAsset(path.join(APP_ROOT), {
        cmd: ['submitter.lambdas.submitter.handler.handler'],
      }),
      architecture: aws_lambda.Architecture.ARM_64,
      timeout: Duration.minutes(5),
      memorySize: 512,
      role,
      environment: {
        ORCABUS_TOKEN_SECRET_ID,
        HOSTNAME_SSM_PARAMETER_NAME,
        WRU_VALIDATOR_LAMBDA_NAME: wruDraftValidatorFunctionName,
        EVENTS_BUS_NAME: EVENT_BUS_NAME,
        TESTDATA_TUMOR_LIBRARY_ID,
        TESTDATA_NORMAL_LIBRARY_ID,
      },
    });

    const submitterApi = new LambdaRestApi(this, 'SubmitterApi', {
      handler: submitterFn,
      proxy: true,
    });

    new CfnOutput(this, 'SubmitterApiUrl', {
      value: submitterApi.url,
      description:
        'Submitter API Gateway endpoint URL (used by docs/operation/SOP/SR.1/generate-WRU-draft.sh)',
    });
  }

  private createWatcherFunction(comparatorFn: IFunction, mainBus: IEventBus): void {
    const role = new Role(this, 'WatcherRole', {
      assumedBy: new ServicePrincipal('lambda.amazonaws.com'),
      description: 'Lambda execution role for SashRegression Watcher',
    });
    role.addManagedPolicy(
      ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole')
    );

    // Async-invoke Comparator on SUCCEEDED runs
    role.addToPolicy(
      new PolicyStatement({
        actions: ['lambda:InvokeFunction'],
        resources: [comparatorFn.functionArn],
      })
    );

    const watcherFn = new DockerImageFunction(this, 'WatcherFunction', {
      code: DockerImageCode.fromImageAsset(path.join(APP_ROOT), {
        cmd: ['watcher.lambdas.watcher.handler.handler'],
      }),
      architecture: aws_lambda.Architecture.ARM_64,
      timeout: Duration.minutes(5),
      memorySize: 512,
      role,
      environment: {
        COMPARATOR_FUNCTION_NAME: comparatorFn.functionName,
      },
    });

    new Rule(this, 'WatcherRule', {
      eventBus: mainBus,
      eventPattern: {
        source: ['orcabus.workflowmanager'],
        detailType: ['WorkflowRunStateChange'],
        detail: {
          workflowRunName: [{ prefix: 'umccr_tested_' }],
        },
      },
      targets: [new LambdaFunction(watcherFn)],
    });
  }
}
