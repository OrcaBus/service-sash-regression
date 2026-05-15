import { StageName } from '@orcabus/platform-cdk-constructs/shared-config/accounts';
import { HelloWorldStackProps } from './deployment-stack';
import { EVENT_BUS } from './constants';

export const getStackProps = (stage: StageName): HelloWorldStackProps => {
  return {
    mainBusName: EVENT_BUS,
    stage: stage,
  };
};
