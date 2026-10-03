#!/usr/bin/env bash

set -euo pipefail

# This script manages Compose services only. The development backend is still
# started separately with uvicorn, as documented in specs/002-cli/quickstart.md.

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "$PROJECT_ROOT"

BASE_COMPOSE_FILE="$PROJECT_ROOT/docker-compose.yml"
DEV_OVERRIDE_FILE="$PROJECT_ROOT/docker-compose.override.yml"
PROD_COMPOSE_FILE="$PROJECT_ROOT/docker-compose.prod.yml"
ENV_FILE="$PROJECT_ROOT/backend/.env"

COMPOSE_BIN=()
COMPOSE_FILES=()
COMPOSE_ENV_ARGS=()
TARGET="infra"

log() {
  printf '[services] %s\n' "$*"
}

die() {
  printf '[services] ERROR: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
用法:
  ./services.sh <command> [infra|prod]
  ./services.sh logs [prod] [service]

命令:
  start [infra|prod]       启动 Compose 服务（默认 infra）
  stop [infra|prod]        停止 Compose 服务
  restart [infra|prod]     重启 Compose 服务
  status [infra|prod]      显示 Compose 状态和健康状态
  logs [service]            跟踪 infra 的全部日志或指定服务日志
  logs prod <service>       跟踪 prod 配置中全部或指定服务日志
  clean [infra|prod]       停止服务并删除当前 Compose 项目的卷（危险）
  infra                     启动开发中间件（postgres、minio、etcd、minio-milvus、milvus）
  prod                      启动生产 Compose 配置
  help                      显示本帮助

脚本管理范围:
  infra  = Compose 中间件；开发 backend/frontend 不由本脚本启动
  backend/frontend = 业务进程，按项目现有方式单独启动
  prod   = Compose base + production override（包含 backend）

注意:
  执行 clean 时会要求手动输入 DELETE DATA 以二次确认删除操作。
EOF
}

check_docker() {
  command -v docker >/dev/null 2>&1 || die "未找到 docker，请先安装并启动 Docker。"

  if docker compose version >/dev/null 2>&1; then
    COMPOSE_BIN=(docker compose)
  elif command -v docker-compose >/dev/null 2>&1; then
    log "docker compose 不可用，兼容使用旧版 docker-compose。"
    COMPOSE_BIN=(docker-compose)
  else
    die "未找到可用的 docker compose 或 docker-compose。"
  fi

  "${COMPOSE_BIN[@]}" version >/dev/null 2>&1 || die "Compose 命令不可用。"
  docker info >/dev/null 2>&1 || die "Docker CLI 存在，但 Docker daemon 未运行。"
}

configure_target() {
  local target="${1:-infra}"

  case "$target" in
    infra)
      TARGET="infra"
      COMPOSE_FILES=("$BASE_COMPOSE_FILE" "$DEV_OVERRIDE_FILE")
      ;;
    prod)
      TARGET="prod"
      COMPOSE_FILES=("$BASE_COMPOSE_FILE" "$PROD_COMPOSE_FILE")
      ;;
    *)
      die "未知生命周期 '$target'，只支持 infra 或 prod。"
      ;;
  esac

  [[ -f "$ENV_FILE" ]] || die "$TARGET 配置要求存在 $ENV_FILE，作为 Compose 与 host backend 的唯一变量来源。"
  COMPOSE_ENV_ARGS=(--env-file "$ENV_FILE")

  [[ -f "${COMPOSE_FILES[0]}" ]] || die "缺少 Compose 文件：${COMPOSE_FILES[0]}"
  [[ -f "${COMPOSE_FILES[1]}" ]] || die "缺少 Compose 文件：${COMPOSE_FILES[1]}"
}

compose() {
  "${COMPOSE_BIN[@]}" \
    --project-directory "$PROJECT_ROOT" \
    "${COMPOSE_ENV_ARGS[@]}" \
    -f "${COMPOSE_FILES[0]}" \
    -f "${COMPOSE_FILES[1]}" \
    "$@"
}

service_names() {
  compose config --services
}

validate_config() {
  log "验证 $TARGET Compose 配置和服务解析。"
  compose config --quiet
  local services
  services="$(service_names)"
  [[ -n "$services" ]] || die "$TARGET Compose 配置没有服务。"
  log "服务：$(printf '%s' "$services" | tr '\n' ' ')"
}

container_id() {
  local service="$1"
  compose ps -aq "$service" 2>/dev/null | awk 'NF { print; exit }'
}

container_info() {
  local cid="$1"
  docker inspect --format '{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$cid" 2>/dev/null || true
}

print_health_status() {
  local service="$1"
  local cid info state health
  cid="$(container_id "$service")"

  if [[ -z "$cid" ]]; then
    printf '  %-16s 未创建\n' "$service"
    return 0
  fi

  info="$(container_info "$cid")"
  if [[ -z "$info" ]]; then
    printf '  %-16s 无法读取容器状态\n' "$service"
    return 0
  fi

  state="${info%%|*}"
  health="${info#*|}"
  if [[ "$health" == "none" ]]; then
    printf '  %-16s %-10s 没有健康检查\n' "$service" "$state"
  else
    printf '  %-16s %-10s health=%s\n' "$service" "$state" "$health"
  fi
}

status_report() {
  local service
  log "Compose 状态（${TARGET}）："
  compose ps -a || true
  log "服务健康状态（仅以容器实际 Health 状态为准）："
  while IFS= read -r service; do
    [[ -n "$service" ]] || continue
    print_health_status "$service"
  done < <(service_names)
}

wait_for_health() {
  local attempt max_attempts=30 service cid info state health
  local has_health=0 pending=0 failed=0 no_health=0

  log "等待 Compose 健康检查完成（最多 ${max_attempts} 次，每次 2 秒）。"
  for ((attempt = 1; attempt <= max_attempts; attempt++)); do
    pending=0
    failed=0
    has_health=0
    no_health=0

    while IFS= read -r service; do
      [[ -n "$service" ]] || continue
      cid="$(container_id "$service")"
      if [[ -z "$cid" ]]; then
        pending=1
        continue
      fi

      info="$(container_info "$cid")"
      state="${info%%|*}"
      health="${info#*|}"
      if [[ "$health" == "none" ]]; then
        no_health=$((no_health + 1))
        [[ "$state" == "running" ]] || failed=1
        continue
      fi

      has_health=1
      case "$health" in
        healthy) ;;
        starting) pending=1 ;;
        unhealthy) failed=1 ;;
        *) pending=1 ;;
      esac
      [[ "$state" == "running" ]] || failed=1
    done < <(service_names)

    if (( failed == 1 )); then
      log "至少一个服务未运行或健康检查失败。"
      status_report
      return 1
    fi
    if (( pending == 0 )); then
      if (( has_health == 0 )); then
        log "容器正在运行，但服务没有健康检查，服务可用性未验证。"
      elif (( no_health > 0 )); then
        log "有健康检查的服务已通过；另有服务没有健康检查，服务可用性未完全验证。"
      else
        log "所有可用健康检查均已通过。"
      fi
      return 0
    fi
    sleep 2
  done

  log "健康检查在有限轮询次数内未完成；不会报告所有服务已启动。"
  status_report
  return 1
}

start_services() {
  check_docker
  validate_config
  log "启动 $TARGET Compose 服务。"
  if [[ "$TARGET" == "prod" ]]; then
    compose up -d --build "$@"
  else
    compose up -d "$@"
  fi
  wait_for_health
}

stop_services() {
  check_docker
  validate_config
  log "停止 $TARGET Compose 服务。"
  compose stop
  log "服务已停止；持久化卷未删除。"
}

restart_services() {
  check_docker
  validate_config
  log "以幂等方式重新启动 $TARGET Compose 服务（处理未创建、已停止或配置变化的容器）。"
  if [[ "$TARGET" == "prod" ]]; then
    compose up -d --build
  else
    compose up -d
  fi
  wait_for_health
}

logs_services() {
  check_docker
  validate_config
  log "查看 $TARGET Compose 日志；按 Ctrl-C 退出日志跟踪。"
  compose logs -f "$@"
}

clean_services() {
  check_docker
  validate_config
  log "危险操作：将停止 $TARGET Compose 项目，并删除它管理的容器、网络和持久化卷。"
  log "将删除的 Compose 卷："
  compose config --volumes | sed 's/^/  - /'
  printf '确认删除上述数据，请输入 DELETE DATA： '
  local confirmation
  read -r confirmation
  [[ "$confirmation" == "DELETE DATA" ]] || die "未确认删除，已取消；未删除任何数据。"
  compose down --volumes
  log "已删除当前 Compose 项目管理的容器、网络和卷；项目目录外文件未删除。"
}

main() {
  local command="${1:-help}"
  shift || true

  case "$command" in
    help|-h|--help)
      usage
      ;;
    infra)
      [[ $# -eq 0 ]] || die "infra 不接受额外参数。"
      configure_target infra
      start_services
      ;;
    prod)
      [[ $# -eq 0 ]] || die "prod 不接受额外参数。"
      configure_target prod
      start_services
      ;;
    start|stop|restart|status|clean)
      local target="${1:-infra}"
      shift || true
      [[ $# -eq 0 ]] || die "$command 不接受额外参数。"
      configure_target "$target"
      case "$command" in
        start) start_services ;;
        stop) stop_services ;;
        restart) restart_services ;;
        status) check_docker; validate_config; status_report ;;
        clean) clean_services ;;
      esac
      ;;
    logs)
      local target="infra"
      if [[ "${1:-}" == "prod" ]]; then
        target="prod"
        shift
      fi
      configure_target "$target"
      logs_services "$@"
      ;;
    *)
      usage >&2
      die "未知命令 '$command'。"
      ;;
  esac
}

main "$@"
