#!/usr/bin/env bash
# Update an ECS service to a new image, wait for stability, run ingest, or roll back.
# Relies on the GitHub OIDC deploy role in infra/terraform/github_oidc.tf.
set -euo pipefail

CLUSTER="${ECS_CLUSTER:?ECS_CLUSTER is required}"
REGION="${AWS_REGION:?AWS_REGION is required}"
export AWS_DEFAULT_REGION="$REGION"
export AWS_PAGER=""

usage() {
  cat <<'EOF'
Usage:
  ecs-deploy.sh current <service>              Print the running task-definition ARN
  ecs-deploy.sh update  <service> <image>      Register a new revision with <image> and deploy it
  ecs-deploy.sh wait    <service> [<service>]  Block until services are stable
  ecs-deploy.sh rollback <backend-arn> <frontend-arn>
  ecs-deploy.sh ingest  <image>                Run the one-off ingest task and wait for it
EOF
  exit 2
}

current_td() {
  local service="$1"
  aws ecs describe-services \
    --cluster "$CLUSTER" \
    --services "$service" \
    --query 'services[0].taskDefinition' \
    --output text
}

# Describe the currently running task definition, swap the first container's image, register, deploy.
update_service() {
  local service="$1" image="$2"
  local family current payload tmp new_arn
  family="$(family_for "$service")"
  current="$(current_td "$service")"
  echo "::notice::$service currently on $current"

  tmp="$(mktemp)"
  aws ecs describe-task-definition --task-definition "$current" \
    --query taskDefinition --output json \
    | jq --arg IMAGE "$image" '
        del(
          .taskDefinitionArn, .revision, .status,
          .requiresAttributes, .compatibilities,
          .registeredAt, .registeredBy, .deregisteredAt, .tags
        )
        | .containerDefinitions[0].image = $IMAGE
      ' >"$tmp"

  new_arn="$(
    aws ecs register-task-definition --cli-input-json "file://$tmp" \
      --query 'taskDefinition.taskDefinitionArn' --output text
  )"
  rm -f "$tmp"
  echo "::notice::$service registered $new_arn"

  aws ecs update-service \
    --cluster "$CLUSTER" \
    --service "$service" \
    --task-definition "$new_arn" \
    --force-new-deployment \
    --query 'service.serviceName' \
    --output text >/dev/null
  echo "$new_arn"
}

family_for() {
  case "$1" in
    backend)  echo "${BACKEND_TASK_FAMILY:?BACKEND_TASK_FAMILY is required}" ;;
    frontend) echo "${FRONTEND_TASK_FAMILY:?FRONTEND_TASK_FAMILY is required}" ;;
    ingest)   echo "${INGEST_TASK_FAMILY:?INGEST_TASK_FAMILY is required}" ;;
    *) echo "unknown service $1" >&2; exit 1 ;;
  esac
}

wait_stable() {
  echo "::notice::waiting for $* to stabilize"
  aws ecs wait services-stable --cluster "$CLUSTER" --services "$@"
}

rollback() {
  local backend_arn="$1" frontend_arn="$2"
  echo "::warning::rolling back backend -> $backend_arn"
  aws ecs update-service --cluster "$CLUSTER" --service backend \
    --task-definition "$backend_arn" --force-new-deployment >/dev/null
  echo "::warning::rolling back frontend -> $frontend_arn"
  aws ecs update-service --cluster "$CLUSTER" --service frontend \
    --task-definition "$frontend_arn" --force-new-deployment >/dev/null
  wait_stable backend frontend
}

run_ingest() {
  local image="$1"
  local family payload tmp new_arn task_arn exit_code
  family="${INGEST_TASK_FAMILY:?INGEST_TASK_FAMILY is required}"
  : "${INGEST_SUBNETS:?INGEST_SUBNETS is required}"
  : "${INGEST_SECURITY_GROUP:?INGEST_SECURITY_GROUP is required}"

  tmp="$(mktemp)"
  aws ecs describe-task-definition --task-definition "$family" \
    --query taskDefinition --output json \
    | jq --arg IMAGE "$image" '
        del(
          .taskDefinitionArn, .revision, .status,
          .requiresAttributes, .compatibilities,
          .registeredAt, .registeredBy, .deregisteredAt, .tags
        )
        | .containerDefinitions[0].image = $IMAGE
      ' >"$tmp"
  new_arn="$(
    aws ecs register-task-definition --cli-input-json "file://$tmp" \
      --query 'taskDefinition.taskDefinitionArn' --output text
  )"
  rm -f "$tmp"

  local subnet_json
  subnet_json="$(printf '%s' "$INGEST_SUBNETS" | jq -R 'split(",")')"

  payload="$(
    jq -n \
      --arg cluster "$CLUSTER" \
      --arg td "$new_arn" \
      --argjson subnets "$subnet_json" \
      --arg sg "$INGEST_SECURITY_GROUP" \
      '{
         cluster: $cluster,
         taskDefinition: $td,
         launchType: "FARGATE",
         count: 1,
         startedBy: "github-actions",
         networkConfiguration: {
           awsvpcConfiguration: {
             subnets: $subnets,
             securityGroups: [$sg],
             assignPublicIp: "ENABLED"
           }
         }
       }'
  )"

  echo "::notice::running ingest $new_arn"
  local run_out
  run_out="$(aws ecs run-task --cli-input-json "$payload")"
  if [[ "$(jq '.failures | length' <<<"$run_out")" != "0" ]]; then
    echo "::error::ingest RunTask failed: $run_out"
    exit 1
  fi
  task_arn="$(jq -r '.tasks[0].taskArn' <<<"$run_out")"
  echo "::notice::ingest task $task_arn"
  aws ecs wait tasks-stopped --cluster "$CLUSTER" --tasks "$task_arn"
  exit_code="$(
    aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$task_arn" \
      --query 'tasks[0].containers[0].exitCode' --output text
  )"
  if [[ "$exit_code" != "0" ]]; then
    echo "::error::ingest exited $exit_code"
    if [[ -n "${INGEST_LOG_GROUP:-}" ]]; then
      aws logs tail "$INGEST_LOG_GROUP" --since 15m --format short || true
    fi
    exit 1
  fi
  echo "::notice::ingest finished"
}

cmd="${1:-}"
shift || true
case "$cmd" in
  current)  current_td "${1:?service}" ;;
  update)   update_service "${1:?service}" "${2:?image}" ;;
  wait)     wait_stable "$@" ;;
  rollback) rollback "${1:?backend-arn}" "${2:?frontend-arn}" ;;
  ingest)   run_ingest "${1:?image}" ;;
  *)        usage ;;
esac
