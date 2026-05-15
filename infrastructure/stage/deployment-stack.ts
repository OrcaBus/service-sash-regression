import * as path from 'path';
import { Construct } from 'constructs';
import { DockerImageCode, DockerImageFunction } from 'aws-cdk-lib/aws-lambda';
import { aws_lambda, Duration, Size, Stack, StackProps } from 'aws-cdk-lib';
import { ManagedPolicy, PolicyStatement, Role, ServicePrincipal } from 'aws-cdk-lib/aws-iam';
import { APP_ROOT, TESTDATA_BUCKET, TESTDATA_CONFIG_S3_URI, RESULT_S3_PREFIX } from './constants';

export interface SashRegressionStackProps extends StackProps {
  stage: string;
}

export class SashRegressionStack extends Stack {
  private readonly lambdaRole: Role;

  constructor(scope: Construct, id: string, props: SashRegressionStackProps) {
    super(scope, id, props);

    this.lambdaRole = new Role(this, 'LambdaRole', {
      assumedBy: new ServicePrincipal('lambda.amazonaws.com'),
      description: 'Lambda execution role for SashRegression comparator',
    });
    this.lambdaRole.addManagedPolicy(
      ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole')
    );

    // Read sash outputs from pipeline cache buckets
    this.lambdaRole.addToPolicy(
      new PolicyStatement({
        actions: ['s3:GetObject', 's3:ListBucket'],
        resources: [
          'arn:aws:s3:::pipeline-*-cache-*',
          'arn:aws:s3:::pipeline-*-cache-*/*',
        ],
      })
    );

    // Read config + write results to testdata bucket
    this.lambdaRole.addToPolicy(
      new PolicyStatement({
        actions: ['s3:GetObject', 's3:PutObject', 's3:ListBucket'],
        resources: [
          `arn:aws:s3:::${TESTDATA_BUCKET}`,
          `arn:aws:s3:::${TESTDATA_BUCKET}/*`,
        ],
      })
    );

    this.createComparatorFunction();
  }

  private createComparatorFunction(): void {
    new DockerImageFunction(this, 'ComparatorFunction', {
      code: DockerImageCode.fromImageAsset(path.join(APP_ROOT)),
      architecture: aws_lambda.Architecture.ARM_64,
      timeout: Duration.minutes(15),
      memorySize: 4096,
      ephemeralStorageSize: Size.gibibytes(10),
      role: this.lambdaRole,
      environment: {
        TESTDATA_CONFIG_S3_URI: TESTDATA_CONFIG_S3_URI,
        RESULT_S3_PREFIX: RESULT_S3_PREFIX,
      },
    });
  }
}
