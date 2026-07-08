import * as path from 'path';
import { StageName } from '@orcabus/platform-cdk-constructs/shared-config/accounts';
import { EVENT_BUS_NAME as ORCABUS_EVENT_BUS_NAME } from '@orcabus/platform-cdk-constructs/shared-config/event-bridge';
import { JWT_SECRET_NAME } from '@orcabus/platform-cdk-constructs/shared-config/secrets';

/** Absolute path to the Python app directory (contains Dockerfile) */
export const APP_ROOT = path.join(__dirname, '../../app');

/**
 * testdata bucket is READ-ONLY for this service — it provides baseline reference data only.
 * Comparison results ALWAYS go to umccr-research-dev regardless of stage.
 * Admin promotes validated results to testdata manually (one-way archive flow).
 */
export const TESTDATA_BUCKET = 'test-data-503977275616-ap-southeast-2';
export const RESULTS_BUCKET = 'umccr-research-dev';

/** OrcaBus shared secrets and SSM — sourced from platform-cdk-constructs shared config */
export const ORCABUS_TOKEN_SECRET_ID = JWT_SECRET_NAME; // "orcabus/token-service-jwt"
export const HOSTNAME_SSM_PARAMETER_NAME = '/hosted_zone/umccr/name';

/** Shared OrcaBus EventBridge bus name — sourced from platform-cdk-constructs shared config */
export const EVENT_BUS_NAME = ORCABUS_EVENT_BUS_NAME; // "OrcaBusMain"

export const TESTDATA_TUMOR_LIBRARY_ID = 'L2301218';
export const TESTDATA_NORMAL_LIBRARY_ID = 'L2301217';

const CONFIG_KEY = 'quentin/sash-regression/config/testdata-cases.yaml';
const RESULT_KEY_PREFIX = 'sash-regression';

/**
 * WruDraftValidator function names per stage.
 * These are the deployed WRU validator Lambda names in each environment.
 */
const WRU_VALIDATOR_FUNCTION_NAME: Record<StageName, string> = {
  BETA: 'OrcaBusBeta-WruValidatorS-WruDraftValidatorCE0E33B-qPMdDh7awGuX', // pragma: allowlist secret
  GAMMA: 'OrcaBusBeta-WruValidatorS-WruDraftValidatorCE0E33B-qPMdDh7awGuX', // pragma: allowlist secret
  PROD: 'OrcaBusBeta-WruValidatorS-WruDraftValidatorCE0E33B-qPMdDh7awGuX', // pragma: allowlist secret
};

/**
 * ICA project + pipeline cache bucket for sash DRAFT engineParameters, per stage.
 * Verified against a real SUCCEEDED sash run in BETA/dev (2026-07-08). GAMMA/PROD values
 * are placeholders pending confirmation — see service-sash-regression#5.
 */
const ICA_PROJECT_ID: Record<StageName, string> = {
  BETA: 'ea19a3f5-ec7c-4940-a474-c31cd91dbad4',
  GAMMA: 'ea19a3f5-ec7c-4940-a474-c31cd91dbad4',
  PROD: 'ea19a3f5-ec7c-4940-a474-c31cd91dbad4',
};

const PIPELINE_CACHE_BUCKET: Record<StageName, string> = {
  BETA: 'pipeline-dev-cache-503977275616-ap-southeast-2',
  GAMMA: 'pipeline-dev-cache-503977275616-ap-southeast-2',
  PROD: 'pipeline-dev-cache-503977275616-ap-southeast-2',
};

export const getStageConstants = (stage: StageName) => {
  return {
    testdataConfigS3Uri: `s3://${RESULTS_BUCKET}/${CONFIG_KEY}`,
    resultS3Prefix: `s3://${RESULTS_BUCKET}/${RESULT_KEY_PREFIX}`,
    wruDraftValidatorFunctionName: WRU_VALIDATOR_FUNCTION_NAME[stage],
    icaProjectId: ICA_PROJECT_ID[stage],
    pipelineCacheBucket: PIPELINE_CACHE_BUCKET[stage],
  };
};
