import * as path from 'path';
import { Construct } from 'constructs';
import { Architecture } from 'aws-cdk-lib/aws-lambda';
import { EventBus, IEventBus, Rule } from 'aws-cdk-lib/aws-events';
import { LambdaFunction } from 'aws-cdk-lib/aws-events-targets';
import { aws_lambda, Duration, Stack, StackProps } from 'aws-cdk-lib';
import { PythonFunction, PythonLayerVersion } from '@aws-cdk/aws-lambda-python-alpha';
import { ManagedPolicy, PolicyStatement, Role, ServicePrincipal } from 'aws-cdk-lib/aws-iam';
import {
  APP_ROOT,
  INCOMING_DETAIL_TYPE,
  INCOMING_EVENT_SOURCE,
  INCOMING_WORKFLOW_NAME,
} from './constants';

export interface HelloWorldStackProps extends StackProps {
  mainBusName: string;
  stage: string;
}

export class HelloWorldStack extends Stack {
  private readonly lambdaRuntimePythonVersion: aws_lambda.Runtime = aws_lambda.Runtime.PYTHON_3_12;
  private readonly mainBus: IEventBus;
  private readonly lambdaRole: Role;
  private readonly baseLayer: PythonLayerVersion;

  constructor(scope: Construct, id: string, props: HelloWorldStackProps) {
    super(scope, id, props);

    this.mainBus = EventBus.fromEventBusName(this, 'OrcaBusMain', props.mainBusName);

    // Shared Lambda execution role
    this.lambdaRole = new Role(this, 'LambdaRole', {
      assumedBy: new ServicePrincipal('lambda.amazonaws.com'),
      description: 'Lambda execution role for HelloWorld service',
    });
    this.lambdaRole.addManagedPolicy(
      ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole')
    );

    // Allow the Lambda to emit events onto the bus
    this.lambdaRole.addToPolicy(
      new PolicyStatement({
        actions: ['events:PutEvents'],
        resources: [this.mainBus.eventBusArn],
      })
    );

    // Layer bundles the Python dependencies from requirements.txt
    this.baseLayer = new PythonLayerVersion(this, 'BaseLayer', {
      entry: path.join(APP_ROOT),
      compatibleRuntimes: [this.lambdaRuntimePythonVersion],
      compatibleArchitectures: [Architecture.ARM_64],
      description: 'Hello World service dependencies',
    });

    this.createHelloWorldFunction(props.mainBusName);
  }

  private createHelloWorldFunction(mainBusName: string): void {
    const helloWorldFn = new PythonFunction(this, 'HelloWorldFunction', {
      entry: path.join(APP_ROOT),
      runtime: this.lambdaRuntimePythonVersion,
      architecture: Architecture.ARM_64,
      index: 'hello_world/lambdas/handler.py',
      handler: 'lambda_handler',
      timeout: Duration.seconds(28),
      memorySize: 512,
      layers: [this.baseLayer],
      role: this.lambdaRole,
      environment: {
        EVENT_BUS_NAME: mainBusName,
      },
    });

    // EventBridge rule: route WorkflowRunStateChange events for the hello-world workflow
    const rule = new Rule(this, 'WorkflowRunStateChangeRule', {
      eventBus: this.mainBus,
      eventPattern: {
        source: [INCOMING_EVENT_SOURCE],
        detailType: [INCOMING_DETAIL_TYPE],
        detail: {
          workflow: {
            name: [INCOMING_WORKFLOW_NAME],
          },
        },
      },
    });

    rule.addTarget(new LambdaFunction(helloWorldFn));
  }
}
