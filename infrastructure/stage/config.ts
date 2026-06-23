import { StageName } from '@orcabus/platform-cdk-constructs/shared-config/accounts';
import { SashRegressionStackProps } from './deployment-stack';

export const getStackProps = (stage: StageName): SashRegressionStackProps => {
  return {
    stage: stage,
  };
};
