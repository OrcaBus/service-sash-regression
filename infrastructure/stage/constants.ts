import * as path from 'path';
import { StageName } from '@orcabus/platform-cdk-constructs/shared-config/accounts';

/** Absolute path to the Python app directory (contains Dockerfile) */
export const APP_ROOT = path.join(__dirname, '../../app');

/**
 * testdata bucket is READ-ONLY for this service — it provides baseline reference data only.
 * Comparison results ALWAYS go to umccr-research-dev regardless of stage.
 * Admin promotes validated results to testdata manually (one-way archive flow).
 */
export const TESTDATA_BUCKET = 'test-data-503977275616-ap-southeast-2';
export const RESULTS_BUCKET = 'umccr-research-dev';

const CONFIG_KEY = 'quentin/sash-regression/config/testdata-cases.yaml';
const RESULT_KEY_PREFIX = 'sash-regression';

// eslint-disable-next-line @typescript-eslint/no-unused-vars
export const getStageConstants = (_stage: StageName) => {
  return {
    testdataConfigS3Uri: `s3://${RESULTS_BUCKET}/${CONFIG_KEY}`,
    resultS3Prefix: `s3://${RESULTS_BUCKET}/${RESULT_KEY_PREFIX}`,
  };
};
