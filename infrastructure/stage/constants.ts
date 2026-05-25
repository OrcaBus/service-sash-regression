import * as path from 'path';
import { StageName } from '@orcabus/platform-cdk-constructs/shared-config/accounts';

/** Absolute path to the Python app directory (contains Dockerfile) */
export const APP_ROOT = path.join(__dirname, '../../app');

/** BETA writes to the research-dev bucket; GAMMA/PROD write to the testdata bucket */
export const RESULT_BUCKET_BY_STAGE: Record<StageName, string> = {
  BETA: 'umccr-research-dev',
  GAMMA: 'test-data-503977275616-ap-southeast-2',
  PROD: 'test-data-503977275616-ap-southeast-2',
};

const CONFIG_KEY = 'testdata/config/sash-regression/testdata-cases.yaml';
const RESULT_KEY_PREFIX = 'testdata/analysis/production/sash-regression';

export const getStageConstants = (stage: StageName) => {
  const bucket = RESULT_BUCKET_BY_STAGE[stage];
  return {
    resultBucket: bucket,
    testdataConfigS3Uri: `s3://${bucket}/${CONFIG_KEY}`,
    resultS3Prefix: `s3://${bucket}/${RESULT_KEY_PREFIX}`,
  };
};
