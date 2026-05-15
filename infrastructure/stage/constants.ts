import * as path from 'path';

/** Absolute path to the Python app directory (contains Dockerfile) */
export const APP_ROOT = path.join(__dirname, '../../app');

/** Testdata S3 bucket */
export const TESTDATA_BUCKET = 'test-data-503977275616-ap-southeast-2';

/** S3 URI of the testdata cases config (uploaded separately) */
export const TESTDATA_CONFIG_S3_URI = `s3://${TESTDATA_BUCKET}/testdata/config/sash-regression/testdata-cases.yaml`;

/** S3 prefix where comparison results are written */
export const RESULT_S3_PREFIX = `s3://${TESTDATA_BUCKET}/testdata/analysis/production/sash`;
