import * as path from 'path';
import { Construct } from 'constructs';
import { DockerImageCode, DockerImageFunction, Function } from 'aws-cdk-lib/aws-lambda';
import { aws_lambda, Duration, Size, Stack, StackProps } from 'aws-cdk-lib';
import { LambdaRestApi } from 'aws-cdk-lib/aws-apigateway';
import { EventBus } from 'aws-cdk-lib/aws-events';
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
  private readonly lambdaRole: Role;

  constructor(scope: Construct, id: string, props: SashRegressionStackProps) {
    super(scope, id, props);

    const { testdataConfigS3Uri, resultS3Prefix, wruDraftValidatorFunctionName } =
      getStageConstants(props.stage as StageName);

    this.lambdaRole = new Role(this, 'LambdaRole', {
      assumedBy: new ServicePrincipal('lambda.amazonaws.com'),
      description: 'Lambda execution role for SashRegression',
    });
    this.lambdaRole.addManagedPolicy(
      ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole')
    );

    // Read sash outputs from pipeline cache buckets and project-data buckets (e.g. project-wgs-accreditation)
    this.lambdaRole.addToPolicy(
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
    this.lambdaRole.addToPolicy(
      new PolicyStatement({
        actions: ['s3:GetObject', 's3:ListBucket'],
        resources: [`arn:aws:s3:::${TESTDATA_BUCKET}`, `arn:aws:s3:::${TESTDATA_BUCKET}/*`],
      })
    );

    // Read config and write results to umccr-research-dev (all stages)
    this.lambdaRole.addToPolicy(
      new PolicyStatement({
        actions: ['s3:GetObject', 's3:PutObject', 's3:ListBucket'],
        resources: [`arn:aws:s3:::${RESULTS_BUCKET}`, `arn:aws:s3:::${RESULTS_BUCKET}/*`],
      })
    );

    // Submitter: read OrcaBus token from Secrets Manager
    this.lambdaRole.addToPolicy(
      new PolicyStatement({
        actions: ['secretsmanager:GetSecretValue'],
        resources: [
          `arn:aws:secretsmanager:${this.region}:${this.account}:secret:${ORCABUS_TOKEN_SECRET_ID}*`,
        ],
      })
    );

    // Submitter: read hostname from SSM
    this.lambdaRole.addToPolicy(
      new PolicyStatement({
        actions: ['ssm:GetParameter'],
        resources: [
          `arn:aws:ssm:${this.region}:${this.account}:parameter${HOSTNAME_SSM_PARAMETER_NAME}`,
        ],
      })
    );

    // Submitter: invoke WruDraftValidator
    const wruFn = Function.fromFunctionName(
      this,
      'WruDraftValidatorFn',
      wruDraftValidatorFunctionName
    );
    this.lambdaRole.addToPolicy(
      new PolicyStatement({
        actions: ['lambda:InvokeFunction'],
        resources: [wruFn.functionArn],
      })
    );

    // Submitter: emit events to OrcaBusMain
    const mainBus = EventBus.fromEventBusName(this, 'OrcaBusMain', EVENT_BUS_NAME);
    this.lambdaRole.addToPolicy(
      new PolicyStatement({
        actions: ['events:PutEvents'],
        resources: [mainBus.eventBusArn],
      })
    );

    this.createComparatorFunction(testdataConfigS3Uri, resultS3Prefix);
    this.createSubmitterFunction(wruDraftValidatorFunctionName);
  }

  private createComparatorFunction(testdataConfigS3Uri: string, resultS3Prefix: string): void {
    new DockerImageFunction(this, 'ComparatorFunction', {
      code: DockerImageCode.fromImageAsset(path.join(APP_ROOT)),
      architecture: aws_lambda.Architecture.ARM_64,
      timeout: Duration.minutes(15),
      memorySize: 4096,
      ephemeralStorageSize: Size.gibibytes(10),
      role: this.lambdaRole,
      environment: {
        TESTDATA_CONFIG_S3_URI: testdataConfigS3Uri,
        RESULT_S3_PREFIX: resultS3Prefix,
      },
    });
  }

  private createSubmitterFunction(wruDraftValidatorFunctionName: string): void {
    const submitterFn = new DockerImageFunction(this, 'SubmitterFunction', {
      code: DockerImageCode.fromImageAsset(path.join(APP_ROOT), {
        cmd: ['submitter.lambdas.submitter.handler.handler'],
      }),
      architecture: aws_lambda.Architecture.ARM_64,
      timeout: Duration.minutes(5),
      memorySize: 512,
      role: this.lambdaRole,
      environment: {
        ORCABUS_TOKEN_SECRET_ID,
        HOSTNAME_SSM_PARAMETER_NAME,
        WRU_VALIDATOR_LAMBDA_NAME: wruDraftValidatorFunctionName,
        EVENTS_BUS_NAME: EVENT_BUS_NAME,
        TESTDATA_TUMOR_LIBRARY_ID,
        TESTDATA_NORMAL_LIBRARY_ID,
      },
    });

    new LambdaRestApi(this, 'SubmitterApi', {
      handler: submitterFn,
      proxy: true,
    });
  }
}
