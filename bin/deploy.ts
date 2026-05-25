#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { StatelessStack } from '../infrastructure/toolchain/stateless-stack';
import { StatefulStack } from '../infrastructure/toolchain/stateful-stack';
import { SashRegressionStack } from '../infrastructure/stage/deployment-stack';
import { getStackProps } from '../infrastructure/stage/config';
import { TOOLCHAIN_ENVIRONMENT } from '@orcabus/platform-cdk-constructs/deployment-stack-pipeline';
import {
  BETA_ACCOUNT_ID,
  PROD_ACCOUNT_ID,
  REGION,
} from '@orcabus/platform-cdk-constructs/shared-config/accounts';

const app = new cdk.App();

const deployMode = app.node.tryGetContext('deployMode');
if (!deployMode) {
  throw new Error("deployMode is required in context (e.g. '-c deployMode=stateless')");
}

if (deployMode === 'stateless') {
  new StatelessStack(app, 'OrcaBusStatelessSashRegressionStack', {
    env: TOOLCHAIN_ENVIRONMENT,
  });
} else if (deployMode === 'stateful') {
  new StatefulStack(app, 'OrcaBusStatefulSashRegressionStack', {
    env: TOOLCHAIN_ENVIRONMENT,
  });
} else if (deployMode === 'beta') {
  new SashRegressionStack(app, 'SashRegressionStack', {
    ...getStackProps('BETA'),
    env: { account: BETA_ACCOUNT_ID, region: REGION },
  });
} else if (deployMode === 'prod') {
  new SashRegressionStack(app, 'SashRegressionStack', {
    ...getStackProps('PROD'),
    env: { account: PROD_ACCOUNT_ID, region: REGION },
  });
} else if (deployMode === 'stage') {
  new SashRegressionStack(app, 'SashRegressionStack', {
    ...getStackProps('BETA'),
    env: { account: '843407916570', region: 'ap-southeast-2' },
  });
} else {
  throw new Error("Invalid 'deployMode' set in the context");
}
