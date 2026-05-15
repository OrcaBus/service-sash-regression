import * as path from 'path';
import { EVENT_BUS_NAME } from '@orcabus/platform-cdk-constructs/shared-config/event-bridge';

/** Absolute path to the Python app directory (contains requirements.txt) */
export const APP_ROOT = path.join(__dirname, '../../app');

/** Shared OrcaBus EventBridge bus name */
export const EVENT_BUS = EVENT_BUS_NAME; // "OrcaBusMain"

/** Events this service consumes */
export const INCOMING_EVENT_SOURCE = 'orcabus.workflowmanager';
export const INCOMING_DETAIL_TYPE = 'WorkflowRunStateChange';
export const INCOMING_WORKFLOW_NAME = 'hello-world';

/** Events this service emits */
export const OUTGOING_EVENT_SOURCE = 'orcabus.helloworld';
