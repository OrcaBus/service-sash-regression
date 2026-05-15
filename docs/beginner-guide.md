# Beginner Guide

This document explains the repository for someone who is new to AWS, Lambda, CDK, and event-driven services.

- [1. What This Repository Is](#1-what-this-repository-is)
- [2. The Big Picture](#2-the-big-picture)
- [3. What Happens End to End](#3-what-happens-end-to-end)
- [4. The Main Technologies, Explained Simply](#4-the-main-technologies-explained-simply)
  - [AWS](#aws)
  - [Lambda](#lambda)
    - [Step 1: Receive the event](#step-1-receive-the-event)
    - [Step 2: Log the raw event](#step-2-log-the-raw-event)
    - [Step 3: Validate the payload](#step-3-validate-the-payload)
    - [Step 4: Build the outgoing event](#step-4-build-the-outgoing-event)
    - [Step 5: Publish the outgoing event](#step-5-publish-the-outgoing-event)
  - [EventBridge and the OrcaBus Event Bus](#eventbridge-and-the-orcabus-event-bus)
  - [Pydantic](#pydantic)
  - [boto3](#boto3)
  - [IAM](#iam)
  - [CDK](#cdk)
  - [What "stack" Means Here](#what-stack-means-here)
    - [Technology stack](#technology-stack)
    - [CDK stack](#cdk-stack)
- [5. The Repository Structure](#5-the-repository-structure)
  - [Root level](#root-level)
  - [Application code](#application-code)
  - [Infrastructure code](#infrastructure-code)
  - [Tests and CI](#tests-and-ci)
- [6. The Actual AWS Resources Created](#6-the-actual-aws-resources-created)
  - [1. OrcaBus event bus reference](#1-orcabus-event-bus-reference)
  - [2. IAM role for Lambda](#2-iam-role-for-lambda)
  - [3. Lambda layer](#3-lambda-layer)
  - [4. Lambda function](#4-lambda-function)
  - [5. EventBridge rule](#5-eventbridge-rule)
- [7. How Deployment Works](#7-how-deployment-works)
  - [Layer 1: The toolchain stack](#layer-1-the-toolchain-stack)
  - [Layer 2: The application stack](#layer-2-the-application-stack)
  - [Why there are multiple deploy modes](#why-there-are-multiple-deploy-modes)
  - [Direct deploy mode](#direct-deploy-mode)
- [8. Local Development, Explained](#8-local-development-explained)
  - [Node.js dependencies](#nodejs-dependencies)
  - [Python dependencies](#python-dependencies)
  - [Typical setup flow](#typical-setup-flow)
- [9. Useful Commands](#9-useful-commands)
  - [Root project](#root-project)
  - [Python app](#python-app)
- [10. What You Would Usually Change When Starting a Real Service](#10-what-you-would-usually-change-when-starting-a-real-service)
- [11. Things That Are Easy to Misunderstand](#11-things-that-are-easy-to-misunderstand)
  - ["The Lambda is the service"](#the-lambda-is-the-service)
  - ["CDK deploys the Python code directly"](#cdk-deploys-the-python-code-directly)
  - ["Stateless means unimportant"](#stateless-means-unimportant)
  - ["There is a complete stateful deployment path already"](#there-is-a-complete-stateful-deployment-path-already)
- [12. File Reading Order for New Team Members](#12-file-reading-order-for-new-team-members)
- [13. Short Glossary](#13-short-glossary)
- [14. One-Sentence Summary](#14-one-sentence-summary)

## 1. What This Repository Is

This repository is a template for a small AWS service.

Its job is:

1. Listen for a specific event on an AWS EventBridge bus.
2. Run a Python Lambda function when that event arrives.
3. Read and validate the event data.
4. Publish a new event back onto the same shared bus.

In this template, the service listens for:

- `WorkflowRunStateChange`
- From source `orcabus.workflowmanager`
- Only when the workflow name is `hello-world`

When that happens, it publishes a new event:

- `HelloWorldEvent`
- From source `orcabus.helloworld`

## 2. The Big Picture

At a high level, this repository has two different parts:

1. A TypeScript infrastructure project.
   This defines what AWS resources should exist.
2. A Python application.
   This contains the actual business logic that runs inside Lambda.

That split is important:

- `TypeScript + CDK` tells AWS what to create.
- `Python + Lambda` is the code that actually runs when an event arrives.

## 3. What Happens End to End

The runtime flow is:

```text
Workflow Manager
  -> sends WorkflowRunStateChange event
  -> OrcaBus event bus (OrcaBusMain)
  -> EventBridge rule filters for workflow.name = hello-world
  -> Lambda function runs
  -> Lambda validates event with Pydantic
  -> Lambda publishes HelloWorldEvent
  -> OrcaBus event bus (OrcaBusMain)
```

So this service is not a web API and it does not wait for HTTP requests.

It is event-driven:

- something happens elsewhere
- an event is emitted
- this service reacts to it

## 4. The Main Technologies, Explained Simply

### AWS

AWS is the cloud platform hosting this service.

The main AWS services used here are:

- `Lambda`: runs the Python code
- `EventBridge`: transports events between services
- `IAM`: controls permissions
- `CloudWatch Logs`: stores Lambda logs
- `CodePipeline`: automates deployment between environments

### Lambda

AWS Lambda is a way to run code without managing a server yourself.

You give AWS a function, and AWS runs it only when needed.

In this repository, the Lambda function lives in:

- `app/hello_world/lambdas/handler.py`

**Configuration** (defined in `infrastructure/stage/deployment-stack.ts`):

- Runtime: Python 3.12
- Architecture: ARM64
- Memory: 512 MB
- Timeout: 28 seconds

**How this Lambda works, step by step:**

#### Step 1: Receive the event

AWS passes the OrcaBus event payload into `lambda_handler(event, context)`.

#### Step 2: Log the raw event

The handler writes the event to logs so debugging is easier.

#### Step 3: Validate the payload

This line is the key validation step:

```python
incoming = IncomingEvent.model_validate(event)
```

That converts the raw dictionary into a typed Pydantic object (defined in `app/hello_world/models.py`).

If the payload shape is wrong, the function fails early instead of silently continuing with bad data.

#### Step 4: Build the outgoing event

The code creates a `HelloWorldEventDetail` object containing:

- `portalRunId`
- `workflowName`
- `status`
- a generated message
- `processedAt`

#### Step 5: Publish the outgoing event

The helper function `emit_event()` calls the OrcaBus event bus using `put_events`.

If the bus reports a failed entry, the function raises an error.

That is intentional:

- success means the event was published
- failure means Lambda invocation fails
- AWS can then retry according to the configured event delivery behavior

### EventBridge and the OrcaBus Event Bus

EventBridge is AWS's event routing system.

Think of it as a central message bus:

- one service publishes an event
- EventBridge receives it
- rules decide which consumers should react

Within the OrcaBus platform, all services communicate through a shared bus called `OrcaBusMain`.

The event this service listens to — `WorkflowRunStateChange` — is published by the [Workflow Manager](https://github.com/OrcaBus/service-workflow-manager), which acts as a gatekeeper for all workflow state changes across the platform. Other services must consume workflow state events from the Workflow Manager only, never directly from execution services.

In this repository:

- the shared bus is the **OrcaBus event bus** (`OrcaBusMain`)
- a rule listens for `WorkflowRunStateChange` from source `orcabus.workflowmanager`
- the rule only matches events where `detail.workflow.name = hello-world`

That filtering happens before Lambda runs, which is useful because the Lambda only wakes up for relevant events.

### Pydantic

Pydantic is a Python library used to validate structured data.

Here, it makes sure the incoming event has the shape the Lambda expects.

The models live in:

- `app/hello_world/models.py`

This helps catch bad or unexpected event payloads early.

### boto3

`boto3` is the AWS SDK for Python.

In this service it is used to call:

- `events.put_events`

That is how the Lambda publishes `HelloWorldEvent` back to EventBridge.

### IAM

IAM controls what AWS resources are allowed to do.

This Lambda has an execution role with permissions for:

- writing logs to CloudWatch
- publishing events to the OrcaBus event bus

Without those permissions, the function could run but fail when trying to log or send events.

### CDK

AWS CDK is an infrastructure-as-code framework.

Instead of manually creating AWS resources in the console, you define them in code.

In this repository, CDK is written in TypeScript.

That means files such as:

- `bin/deploy.ts`
- `infrastructure/stage/deployment-stack.ts`
- `infrastructure/toolchain/stateless-stack.ts`

describe the AWS infrastructure.

### What "stack" Means Here

The word "stack" can be confusing because it has two meanings.

#### Technology stack

This means the set of technologies used by the project.

For this repository, the technology stack is roughly:

- TypeScript
- AWS CDK
- Python
- AWS Lambda
- AWS EventBridge
- IAM
- GitHub Actions

#### CDK stack

In AWS CDK, a stack is a deployable unit of infrastructure.

You can think of a stack as "one package of AWS resources that CDK deploys together".

This repo currently contains these important stack concepts:

- `StatelessStack`
  Creates the deployment pipeline in the toolchain account.
- `HelloWorldStack`
  Creates the actual application resources such as the Lambda, IAM role, layer, and EventBridge rule.
- `StatefulStack`
  Still a placeholder template and not fully configured yet.

## 5. The Repository Structure

### Root level

- `README.md`
  Service overview and deployment notes.
- `bin/deploy.ts`
  Main CDK entrypoint. It decides which stack to create based on `deployMode`.
- `package.json`
  Node/TypeScript dependencies and CDK commands.
- `Makefile`
  Convenience commands for install, lint, and tests.

### Application code

- `app/hello_world/lambdas/handler.py`
  Lambda entrypoint.
- `app/hello_world/models.py`
  Pydantic models for incoming and outgoing event data.
- `app/requirements.txt`
  Python runtime dependencies packaged for Lambda.
- `app/requirements-dev.txt`
  Python development and test dependencies.
- `app/tests/`
  Python tests for the Lambda application.

### Infrastructure code

- `infrastructure/stage/deployment-stack.ts`
  Defines the AWS resources the application needs in one environment.
- `infrastructure/stage/config.ts`
  Supplies stage-specific configuration.
- `infrastructure/stage/constants.ts`
  Central place for event names, bus names, and app paths.
- `infrastructure/toolchain/stateless-stack.ts`
  Defines the deployment pipeline.
- `infrastructure/toolchain/stateful-stack.ts`
  Template placeholder for future stateful resources.

### Tests and CI

- `test/`
  Infrastructure tests, mainly around CDK and `cdk-nag`.
- `.github/workflows/pr-tests.yml`
  Pull request checks run by GitHub Actions.

## 6. The Actual AWS Resources Created

The main application stack is `HelloWorldStack`.

That stack creates:

### 1. OrcaBus event bus reference

The code does not create a new bus.

Instead, it connects to the existing OrcaBus event bus by name:

- `OrcaBusMain`

### 2. IAM role for Lambda

This role is assumed by the Lambda service and gives the function permission to:

- write logs
- call `events:PutEvents`

### 3. Lambda layer

The layer packages Python dependencies from the `app/` directory.

This exists so the Lambda function can use installed libraries such as Pydantic and boto3-related code without manually copying them into every file.

### 4. Lambda function

The function is named `HelloWorldFunction` in CDK.

It runs the Python handler:

- file: `hello_world/lambdas/handler.py`
- function: `lambda_handler`

It also receives this environment variable:

- `EVENT_BUS_NAME`

That tells the function which OrcaBus event bus to publish to.

### 5. EventBridge rule

The rule named `WorkflowRunStateChangeRule` listens to the bus and only forwards matching events to Lambda.

This is important because it means filtering happens in infrastructure, not only in Python code.

## 7. How Deployment Works

Deployment has two layers in this repository.

### Layer 1: The toolchain stack

The `StatelessStack` creates the deployment pipeline.

Its job is not to run the application.

Its job is to create the automation that will later deploy the application into environments such as:

- `beta`
- `gamma`
- `prod`

This is defined in:

- `infrastructure/toolchain/stateless-stack.ts`

### Layer 2: The application stack

The `HelloWorldStack` creates the actual runtime resources:

- Lambda
- IAM role
- Lambda layer
- EventBridge rule

This is defined in:

- `infrastructure/stage/deployment-stack.ts`

### Why there are multiple deploy modes

`bin/deploy.ts` chooses what CDK creates based on `deployMode`.

The important modes are:

- `stateless`
  Creates the pipeline stack.
- `direct`
  Deploys `HelloWorldStack` directly into the current AWS account and region.
- `stateful`
  Placeholder mode for future stateful infrastructure. It is not ready in this template.

### Direct deploy mode

The `direct` mode is useful for manual development or experiments because it bypasses the toolchain pipeline and deploys the application stack directly.

In `direct` mode:

- account comes from `CDK_DEFAULT_ACCOUNT`
- region comes from `CDK_DEFAULT_REGION` or defaults to `ap-southeast-2`
- event bus comes from `EVENT_BUS_NAME` or defaults to `OrcaBusMain`
- stage is set to `dev`

## 8. Local Development, Explained

This repository actually has two dependency systems:

### Node.js dependencies

These are for the CDK and TypeScript side of the project.

Installed from:

- `package.json`

Used for:

- CDK commands
- TypeScript compilation
- infrastructure tests

### Python dependencies

These are for the Lambda application.

Installed from:

- `app/requirements.txt`
- `app/requirements-dev.txt`

Used for:

- Lambda runtime packages
- Python tests
- linting/formatting inside `app/`

### Typical setup flow

From the repository root:

```sh
corepack enable pnpm
make install
```

From the `app/` directory:

```sh
make install
```

That means:

- root install prepares the CDK/TypeScript project
- `app/` install prepares the Python Lambda project

## 9. Useful Commands

### Root project

```sh
make install
make check
make test
```

What they do:

- `make install`
  Installs Node dependencies with `pnpm install --frozen-lockfile`
- `make check`
  Runs security and formatting checks for the root project
- `make test`
  Runs TypeScript compile plus Jest tests

### Python app

From `app/`:

```sh
make install
make check
make test
make fix
```

What they do:

- `make install`
  Installs Python development dependencies
- `make check`
  Runs `ruff` lint checks
- `make test`
  Runs `pytest`
- `make fix`
  Applies `ruff` fixes and formatting

## 10. What You Would Usually Change When Starting a Real Service

This repo is a template, so several values still represent the example service.

Typical changes include:

1. Rename the service from `hello-world` to the real service name.
2. Update incoming and outgoing event names.
3. Replace the example Lambda business logic.
4. Update the GitHub repository and pipeline names in the toolchain stack.
5. Remove or complete the placeholder `StatefulStack` if the service needs databases, buckets, or queues.
6. Adjust tests so they reflect the real resources and rules.

Also search for:

- `TODO:`

There are still template placeholders in the repository.

## 11. Things That Are Easy to Misunderstand

### "The Lambda is the service"

Partly true, but incomplete.

The Lambda is the code that runs, but the service also depends on:

- the EventBridge rule that triggers it
- the IAM role that grants permissions
- the deployment pipeline that ships it
- the event bus it listens to and publishes to

### "CDK deploys the Python code directly"

Not exactly.

CDK defines the Lambda resource and packages the application code so AWS can run it, but CDK itself is the infrastructure definition layer.

### "Stateless means unimportant"

No.

Stateless just means the service does not keep durable data such as:

- databases
- S3 objects
- queues that hold business state

This service still performs useful work; it just does not store data long term.

### "There is a complete stateful deployment path already"

No.

The repository contains a `StatefulStack` template, but it still has placeholder values and is not ready for use as-is.

## 12. File Reading Order for New Team Members

If someone is completely new to the project, this is the best reading order:

1. `README.md`
2. `docs/beginner-guide.md`
3. `bin/deploy.ts`
4. `infrastructure/stage/deployment-stack.ts`
5. `app/hello_world/lambdas/handler.py`
6. `app/hello_world/models.py`
7. `infrastructure/toolchain/stateless-stack.ts`

That order helps because it goes from overview to infrastructure to runtime logic to deployment automation.

## 13. Short Glossary

- `Event`: a message saying that something happened
- `OrcaBus event bus` (`OrcaBusMain`): the shared channel through which all OrcaBus platform services communicate
- `Rule`: filter that decides which events should trigger which targets
- `Lambda`: serverless function that runs on demand
- `IAM role`: AWS permission identity used by the Lambda
- `CDK`: code that defines AWS resources
- `Stack`: one deployable group of AWS resources
- `Pipeline`: automation that builds and deploys code
- `Stateless`: does not persist business data long term
- `Pydantic`: Python library for validating structured data

## 14. One-Sentence Summary

This repository is a template for an event-driven AWS service where TypeScript CDK defines the cloud resources and a Python Lambda reacts to EventBridge events, validates them, and emits a new event.
