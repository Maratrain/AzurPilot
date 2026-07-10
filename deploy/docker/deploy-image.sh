#!/usr/bin/env bash

set -euo pipefail

detect_owner_home() {
    if [ "$(id -u)" -eq 0 ] && [ -n "${SUDO_USER:-}" ] && [ "${SUDO_USER}" != "root" ]; then
        local owner_home
        if command -v getent >/dev/null 2>&1; then
            owner_home="$(getent passwd "${SUDO_USER}" | cut -d: -f6 || true)"
            if [ -n "${owner_home}" ]; then
                printf '%s\n' "${owner_home}"
                return
            fi
        fi
        if [ -r /etc/passwd ]; then
            owner_home="$(awk -F: -v user="${SUDO_USER}" '$1 == user { print $6; found = 1 } END { exit !found }' /etc/passwd || true)"
            if [ -n "${owner_home}" ]; then
                printf '%s\n' "${owner_home}"
                return
            fi
        fi
    fi
    printf '%s\n' "${HOME}"
}

REPOSITORY="${REPOSITORY:-https://gitcode.com/ddl2/AzurLaneAutoScript}"
IMAGE="${IMAGE:-crpi-gukwnnx8iuh9qpez.cn-shanghai.personal.cr.aliyuncs.com/hajiming/ap:latest}"
OWNER_HOME="${OWNER_HOME:-$(detect_owner_home)}"
APP_DIR="${APP_DIR:-${OWNER_HOME}/AP}"
CONTAINER="AzurPilot"
WEBUI_PORT="${WEBUI_PORT:-}"
DEFAULT_WEBUI_PORT=25548
APP_WORKDIR="/app/AzurPilot"
VENV_VOLUME="${CONTAINER}-venv"
AP_DOCKER_CONFIG="${AP_DOCKER_CONFIG:-${OWNER_HOME}/.azurpilot/docker}"
TOTAL_STEPS=8
CURRENT_STEP=0
LANGUAGE="${LANGUAGE:-}"

BOLD='\033[1m'
DIM='\033[2m'
RED='\033[31m'
GREEN='\033[32m'
YELLOW='\033[33m'
BLUE='\033[34m'
CYAN='\033[36m'
RESET='\033[0m'

t() {
    local key="$1"
    case "${LANGUAGE}:${key}" in
        en:title) printf 'AzurPilot Docker Deployment\n' ;;
        en:subtitle) printf 'Automated installer for AzurPilot WebUI\n' ;;
        en:language_title) printf 'Select language / 选择语言\n' ;;
        en:language_hint) printf '1) 简体中文  2) English\n' ;;
        en:language_prompt) printf 'Please choose [1-2], default 1: ' ;;
        en:invalid_choice) printf 'Invalid choice, using default.\n' ;;
        en:port_title) printf 'WebUI port\n' ;;
        en:port_prompt) printf 'External port, default %s: ' "${DEFAULT_WEBUI_PORT}" ;;
        en:port_invalid) printf 'Invalid port, using default %s.\n' "${DEFAULT_WEBUI_PORT}" ;;
        en:working) printf 'Working' ;;
        en:base_tools) printf 'Preparing base tools\n' ;;
        en:docker_check) printf 'Checking Docker\n' ;;
        en:docker_config) printf 'Preparing Docker client config\n' ;;
        en:docker_missing) printf 'Docker is not installed. Install it from China mirror now? [Y/n]: ' ;;
        en:docker_rejected) printf 'Docker installation cancelled. Please install Docker and rerun this script.\n' ;;
        en:docker_install) printf 'Installing Docker from China mirror\n' ;;
        en:docker_start) printf 'Starting Docker service\n' ;;
        en:source) printf 'Preparing source code\n' ;;
        en:source_update) printf 'Updating source: %s\n' "${APP_DIR}" ;;
        en:source_clone) printf 'Cloning source: %s\n' "${REPOSITORY}" ;;
        en:image_pull) printf 'Pulling Docker image\n' ;;
        en:container_cleanup) printf 'Removing previous container\n' ;;
        en:container_start) printf 'Starting container\n' ;;
        en:container_failed) printf 'Container exited unexpectedly. Recent logs:\n' ;;
        en:network) printf 'Collecting access addresses\n' ;;
        en:done) printf 'Deployment completed\n' ;;
        en:container) printf 'Container\n' ;;
        en:source_dir) printf 'Source\n' ;;
        en:public_url) printf 'Public URL\n' ;;
        en:private_url) printf 'Private URL\n' ;;
        en:webui_password) printf 'WebUI password\n' ;;
        en:no_password) printf 'Not set\n' ;;
        en:wait_webui) printf 'Waiting for WebUI startup\n' ;;
        en:webui_timeout) printf 'WebUI startup timed out. Recent logs:\n' ;;
        en:unsupported_docker) printf 'Unsupported system. Please install Docker manually and rerun this script.\n' ;;
        en:unsupported_tools) printf 'Unsupported system. Please install git and curl manually and rerun this script.\n' ;;
        en:missing_command) printf 'Missing command: %s\n' "${2:-}" ;;
        en:ip_failed) printf 'Unavailable\n' ;;
        zh:title) printf 'AzurPilot Docker 部署向导\n' ;;
        zh:subtitle) printf '为 AzurPilot WebUI 准备运行环境\n' ;;
        zh:language_title) printf 'Select language / 选择语言\n' ;;
        zh:language_hint) printf '1) 简体中文  2) English\n' ;;
        zh:language_prompt) printf '请选择 [1-2]，默认 1：' ;;
        zh:invalid_choice) printf '输入无效，使用默认选项。\n' ;;
        zh:port_title) printf 'WebUI 端口\n' ;;
        zh:port_prompt) printf '对外端口，默认 %s：' "${DEFAULT_WEBUI_PORT}" ;;
        zh:port_invalid) printf '端口无效，使用默认 %s。\n' "${DEFAULT_WEBUI_PORT}" ;;
        zh:working) printf '正在处理' ;;
        zh:base_tools) printf '准备基础工具\n' ;;
        zh:docker_check) printf '检查 Docker\n' ;;
        zh:docker_config) printf '准备 Docker 客户端配置\n' ;;
        zh:docker_missing) printf '检测到未安装 Docker，是否使用国内镜像源自动安装？[Y/n]：' ;;
        zh:docker_rejected) printf '已取消安装 Docker，请手动安装后重新运行脚本。\n' ;;
        zh:docker_install) printf '使用国内镜像源安装 Docker\n' ;;
        zh:docker_start) printf '启动 Docker 服务\n' ;;
        zh:source) printf '准备源码\n' ;;
        zh:source_update) printf '更新源码：%s\n' "${APP_DIR}" ;;
        zh:source_clone) printf '克隆源码：%s\n' "${REPOSITORY}" ;;
        zh:image_pull) printf '拉取 Docker 镜像\n' ;;
        zh:container_cleanup) printf '清理旧容器\n' ;;
        zh:container_start) printf '启动容器\n' ;;
        zh:container_failed) printf '容器启动后异常退出，最近日志如下：\n' ;;
        zh:network) printf '获取访问地址\n' ;;
        zh:done) printf '部署完成\n' ;;
        zh:container) printf '容器名\n' ;;
        zh:source_dir) printf '源码目录\n' ;;
        zh:public_url) printf '公网访问\n' ;;
        zh:private_url) printf '内网访问\n' ;;
        zh:webui_password) printf 'WebUI 密码\n' ;;
        zh:no_password) printf '未设置\n' ;;
        zh:wait_webui) printf '等待 WebUI 完全启动\n' ;;
        zh:webui_timeout) printf '等待 WebUI 启动超时，最近日志如下：\n' ;;
        zh:unsupported_docker) printf '当前系统暂不支持自动安装 Docker，请手动安装后重试。\n' ;;
        zh:unsupported_tools) printf '当前系统暂不支持自动安装基础工具，请手动安装 git 和 curl 后重试。\n' ;;
        zh:missing_command) printf '缺少命令：%s\n' "${2:-}" ;;
        zh:ip_failed) printf '获取失败\n' ;;
        *) printf '%s\n' "$key" ;;
    esac
}

clear_screen() {
    if [ -t 1 ]; then
        printf '\033c'
    fi
}

print_logo() {
    cat <<'EOF'
     _                    ____  _ _       _
    / \    _____   _ _ __|  _ \(_) | ___ | |_
   / _ \  |_  / | | | '__| |_) | | |/ _ \| __|
  / ___ \  / /| |_| | |  |  __/| | | (_) | |_
 /_/_ _\_\/___|\__,_|_|  |_|   |_|_|\___/ \__|
 |_ _| \ | / ___|  / \  | |   | |
  | ||  \| \___ \ / _ \ | |   | |
  | || |\  |___) / ___ \| |___| |___
 |___|_| \_|____/_/   \_\_____|_____|

EOF
}

print_header() {
    printf '%b' "${CYAN}"
    print_logo
    printf '%b' "${RESET}"
    printf '%b\n' "${BOLD}${BLUE}============================================================${RESET}"
    printf '%b\n' "${BOLD}$(t title)${RESET}"
    printf '%b\n' "${DIM}$(t subtitle)${RESET}"
    printf '%b\n' "${BLUE}============================================================${RESET}"
    printf 'Repository : %s\n' "${REPOSITORY}"
    printf 'Image      : %s\n' "${IMAGE}"
    printf 'Source     : %s\n' "${APP_DIR}"
    printf 'Container  : %s\n' "${CONTAINER}"
    printf 'Port       : %s\n' "${WEBUI_PORT}"
    printf '%b\n\n' "${BLUE}------------------------------------------------------------${RESET}"
}

read_tty() {
    local prompt="$1"
    local reply=""
    if [ -r /dev/tty ]; then
        printf '%b' "${prompt}" >/dev/tty
        IFS= read -r reply </dev/tty || true
    fi
    printf '%s' "${reply}"
}

choose_language() {
    clear_screen
    printf '%b\n' "${BOLD}${BLUE}============================================================${RESET}"
    printf '%b\n' "${BOLD}$(LANGUAGE=zh t language_title)${RESET}"
    printf '%b\n' "${BLUE}============================================================${RESET}"
    printf '%s\n' "$(LANGUAGE=zh t language_hint)"

    if [ -z "${LANGUAGE}" ]; then
        local choice
        choice="$(read_tty "$(LANGUAGE=zh t language_prompt)")"
        case "${choice}" in
            2) LANGUAGE="en" ;;
            ""|1) LANGUAGE="zh" ;;
            *)
                LANGUAGE="zh"
                printf '%b%s%b' "${YELLOW}" "$(t invalid_choice)" "${RESET}"
                ;;
        esac
    fi
}

choose_port() {
    local port
    if [ -n "${WEBUI_PORT}" ]; then
        return
    fi

    printf '\n%b%s%b\n' "${BOLD}" "$(t port_title)" "${RESET}"
    port="$(read_tty "$(t port_prompt)")"
    if [ -z "${port}" ]; then
        WEBUI_PORT="${DEFAULT_WEBUI_PORT}"
    elif printf '%s' "${port}" | grep -Eq '^[0-9]+$' && [ "${port}" -ge 1 ] && [ "${port}" -le 65535 ]; then
        WEBUI_PORT="${port}"
    else
        WEBUI_PORT="${DEFAULT_WEBUI_PORT}"
        warn "$(t port_invalid)"
    fi
}

step() {
    CURRENT_STEP=$((CURRENT_STEP + 1))
    clear_screen
    print_header
    printf '\n%b(%s/%s)%b %s\n' "${CYAN}" "${CURRENT_STEP}" "${TOTAL_STEPS}" "${RESET}" "$1"
}

info() {
    printf '%b==>%b %s\n' "${CYAN}" "${RESET}" "$*"
}

success() {
    printf '%bOK%b %s\n' "${GREEN}" "${RESET}" "$*"
}

warn() {
    printf '%bWARN%b %s\n' "${YELLOW}" "${RESET}" "$*"
}

fail() {
    printf '%bERROR%b %s\n' "${RED}" "${RESET}" "$*" >&2
    exit 1
}

run_with_spinner() {
    local message="$1"
    shift

    if [ ! -t 1 ]; then
        "$@"
        return
    fi

    local spin='|/-\'
    local index=0
    local status=0
    local tmp
    tmp="$(mktemp)"

    set +e
    "$@" >"${tmp}" 2>&1 &
    local pid=$!

    while kill -0 "${pid}" 2>/dev/null; do
        printf '\r%b%s%b %s %s' "${CYAN}" "${spin:index++%4:1}" "${RESET}" "${message}" "$(t working)"
        sleep 0.12
    done

    wait "${pid}"
    status=$?
    set -e

    printf '\r%*s\r' 80 ''
    if [ "${status}" -eq 0 ]; then
        success "${message}"
    else
        cat "${tmp}" >&2
        rm -f "${tmp}"
        return "${status}"
    fi
    rm -f "${tmp}"
}

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        fail "$(t missing_command "$1")"
    fi
}

run_as_root() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    else
        sudo "$@"
    fi
}

run_as_owner() {
    if [ "$(id -u)" -eq 0 ] && [ -n "${SUDO_USER:-}" ] && [ "${SUDO_USER}" != "root" ]; then
        sudo -H -u "${SUDO_USER}" "$@"
    else
        "$@"
    fi
}

git_cmd() {
    run_as_owner env GIT_PROGRESS_DELAY=0 git -c "safe.directory=${APP_DIR}" "$@"
}

confirm_docker_install() {
    local answer
    answer="$(read_tty "$(t docker_missing)")"
    case "${answer}" in
        ""|y|Y|yes|YES|Yes|是|好) return 0 ;;
        *) return 1 ;;
    esac
}

install_docker_debian() {
    info "$(t docker_install)"
    run_as_root apt-get update
    run_as_root apt-get install -y ca-certificates curl gnupg lsb-release
    run_as_root install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://mirrors.tuna.tsinghua.edu.cn/docker-ce/linux/debian/gpg \
        | run_as_root gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    run_as_root chmod a+r /etc/apt/keyrings/docker.gpg

    . /etc/os-release
    local distro="${ID}"
    if [ "${ID}" = "ubuntu" ] || [ "${ID_LIKE:-}" = "ubuntu" ]; then
        distro="ubuntu"
    fi

    printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.gpg] https://mirrors.tuna.tsinghua.edu.cn/docker-ce/linux/%s %s stable\n' \
        "$(dpkg --print-architecture)" "${distro}" "${VERSION_CODENAME}" \
        | run_as_root tee /etc/apt/sources.list.d/docker.list >/dev/null

    run_as_root apt-get update
    run_as_root apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
}

install_docker_rhel() {
    info "$(t docker_install)"
    if command -v dnf >/dev/null 2>&1; then
        . /etc/os-release
        local repo="https://mirrors.aliyun.com/docker-ce/linux/centos/docker-ce.repo"
        if [ "${ID}" = "fedora" ]; then
            repo="https://mirrors.aliyun.com/docker-ce/linux/fedora/docker-ce.repo"
        fi

        run_as_root dnf install -y dnf-plugins-core
        if ! run_as_root dnf config-manager addrepo --from-repofile "${repo}"; then
            run_as_root dnf config-manager --add-repo "${repo}"
        fi
        run_as_root dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    else
        run_as_root yum install -y yum-utils
        run_as_root yum-config-manager --add-repo https://mirrors.aliyun.com/docker-ce/linux/centos/docker-ce.repo
        run_as_root yum install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    fi
}

install_docker() {
    if command -v docker >/dev/null 2>&1; then
        success "Docker"
        return
    fi

    if ! confirm_docker_install; then
        fail "$(t docker_rejected)"
    fi

    if command -v apt-get >/dev/null 2>&1; then
        install_docker_debian
    elif command -v yum >/dev/null 2>&1 || command -v dnf >/dev/null 2>&1; then
        install_docker_rhel
    else
        fail "$(t unsupported_docker)"
    fi
}

install_base_tools() {
    if command -v git >/dev/null 2>&1 && command -v curl >/dev/null 2>&1; then
        success "git curl"
        return
    fi

    info "$(t base_tools)"
    if command -v apt-get >/dev/null 2>&1; then
        run_as_root apt-get update
        run_as_root apt-get install -y git curl ca-certificates
    elif command -v yum >/dev/null 2>&1; then
        run_as_root yum install -y git curl ca-certificates
    elif command -v dnf >/dev/null 2>&1; then
        run_as_root dnf install -y git curl ca-certificates
    else
        fail "$(t unsupported_tools)"
    fi
}

start_docker() {
    if command -v systemctl >/dev/null 2>&1; then
        run_as_root systemctl enable --now docker
    else
        run_as_root service docker start || true
    fi
}

docker_cmd() {
    if docker info >/dev/null 2>&1; then
        docker "$@"
    else
        run_as_root docker "$@"
    fi
}

prepare_docker_config() {
    run_as_owner mkdir -p "${AP_DOCKER_CONFIG}"
    if [ ! -f "${AP_DOCKER_CONFIG}/config.json" ]; then
        run_as_owner sh -c 'printf "{}\n" > "$1"' sh "${AP_DOCKER_CONFIG}/config.json"
    fi
    export DOCKER_CONFIG="${AP_DOCKER_CONFIG}"
    info "$(t docker_config)：${DOCKER_CONFIG}"
}

detect_host_timezone() {
    if [ -n "${TZ:-}" ]; then
        printf '%s\n' "${TZ}"
        return
    fi
    if [ -f /etc/timezone ]; then
        sed -n '1p' /etc/timezone | tr -d '\r\n'
        return
    fi
    if [ -L /etc/localtime ]; then
        local localtime
        localtime="$(readlink -f /etc/localtime 2>/dev/null || readlink /etc/localtime)"
        printf '%s\n' "${localtime}" | sed 's#^/usr/share/zoneinfo/##'
        return
    fi
    if command -v timedatectl >/dev/null 2>&1; then
        timedatectl show -p Timezone --value 2>/dev/null || true
    fi
}

prepare_timezone_args() {
    DOCKER_TIMEZONE_ARGS=()
    local host_tz
    host_tz="$(detect_host_timezone)"
    if [ -n "${host_tz}" ]; then
        DOCKER_TIMEZONE_ARGS+=(-e "TZ=${host_tz}")
    fi
    if [ -f /etc/localtime ]; then
        DOCKER_TIMEZONE_ARGS+=(-v "/etc/localtime:/etc/localtime:ro")
    fi
    if [ -f /etc/timezone ]; then
        DOCKER_TIMEZONE_ARGS+=(-v "/etc/timezone:/etc/timezone:ro")
    fi
}

get_public_ip() {
    local url ip
    for url in \
        "https://4.ipw.cn" \
        "https://myip.ipip.net/s" \
        "https://ifconfig.me/ip"; do
        if ip="$(curl -fsSL --max-time 5 "${url}" 2>/dev/null)"; then
            printf '%s\n' "${ip}"
            return
        fi
    done
    t ip_failed
}

get_private_ip() {
    local ip
    if command -v hostname >/dev/null 2>&1; then
        ip="$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | grep -v '^127\.')"
        [ -n "${ip}" ] && printf '%s\n' "${ip}" && return
    fi
    if command -v ip >/dev/null 2>&1; then
        ip="$(ip -4 -o addr show scope global | awk '{print $4}' | cut -d/ -f1)"
        [ -n "${ip}" ] && printf '%s\n' "${ip}" && return
    fi
    t ip_failed
}

read_webui_password() {
    local password_file="${APP_DIR}/password.txt"
    local deploy_file="${APP_DIR}/config/deploy.yaml"
    local password=""

    if [ -f "${password_file}" ]; then
        password="$(sed -n '1p' "${password_file}" | tr -d '\r\n')"
    fi
    if [ -z "${password}" ] && [ -f "${deploy_file}" ]; then
        password="$(sed -n 's/^[[:space:]]*Password[[:space:]]*:[[:space:]]*//p' "${deploy_file}" \
            | sed -n '1p' \
            | sed 's/[[:space:]]*$//; s/^["'\'']//; s/["'\'']$//' \
            | grep -iv '^null$' || true)"
    fi

    if [ -n "${password}" ]; then
        printf '%s\n' "${password}"
    else
        t no_password
    fi
}

wait_webui_ready() {
    local waited=0
    local timeout=120
    local url="http://127.0.0.1:${WEBUI_PORT}"

    while [ "${waited}" -lt "${timeout}" ]; do
        if docker_cmd logs --tail 80 "${CONTAINER}" 2>/dev/null | grep -q "Uvicorn running"; then
            return 0
        fi
        if curl -fsS --max-time 2 "${url}" >/dev/null 2>&1; then
            return 0
        fi
        sleep 2
        waited=$((waited + 2))
    done
    return 1
}

print_urls() {
    local label="$1"
    local ips="$2"
    local printed=0

    while IFS= read -r ip; do
        [ -z "${ip}" ] && continue
        if [ "${printed}" -eq 0 ]; then
            printf '%-12s: http://%s:%s\n' "${label}" "${ip}" "${WEBUI_PORT}"
        else
            printf '%-12s  http://%s:%s\n' "" "${ip}" "${WEBUI_PORT}"
        fi
        printed=1
    done <<EOF
${ips}
EOF

    if [ "${printed}" -eq 0 ]; then
        printf '%-12s: %s\n' "${label}" "$(t ip_failed)"
    fi
}

choose_language
choose_port
clear_screen
print_header

step "$(t base_tools)"
run_with_spinner "$(t base_tools)" install_base_tools
require_command git
require_command curl

step "$(t docker_check)"
install_docker

step "$(t docker_start)"
run_with_spinner "$(t docker_start)" start_docker
prepare_docker_config

step "$(t source)"
if [ -d "${APP_DIR}/.git" ]; then
    info "$(t source_update)"
    git_cmd -C "${APP_DIR}" fetch --all --prune --verbose --progress
    git_cmd -C "${APP_DIR}" pull --ff-only --verbose --progress
else
    info "$(t source_clone)"
    git_cmd clone --verbose --progress "${REPOSITORY}" "${APP_DIR}"
fi

step "$(t image_pull)"
docker_cmd pull "${IMAGE}"

step "$(t container_cleanup)"
if docker_cmd ps -a --format '{{.Names}}' | grep -Fxq "${CONTAINER}"; then
    run_with_spinner "$(t container_cleanup)" docker_cmd rm -f "${CONTAINER}"
else
    success "${CONTAINER}"
fi

step "$(t container_start)"
prepare_timezone_args
run_with_spinner "$(t container_start)" docker_cmd run -d \
    --name "${CONTAINER}" \
    --restart unless-stopped \
    -p "${WEBUI_PORT}:${WEBUI_PORT}" \
    "${DOCKER_TIMEZONE_ARGS[@]}" \
    -v "${APP_DIR}:${APP_WORKDIR}:rw" \
    -v "${VENV_VOLUME}:${APP_WORKDIR}/.venv" \
    -w "${APP_WORKDIR}" \
    "${IMAGE}"

sleep 3
if [ "$(docker_cmd inspect -f '{{.State.Running}}' "${CONTAINER}" 2>/dev/null || printf false)" != "true" ]; then
    warn "$(t container_failed)"
    docker_cmd logs --tail 120 "${CONTAINER}" || true
    exit 1
fi

run_with_spinner "$(t wait_webui)" wait_webui_ready || {
    warn "$(t webui_timeout)"
    docker_cmd logs --tail 120 "${CONTAINER}" || true
    exit 1
}

step "$(t network)"
PUBLIC_IP="$(get_public_ip)"
PRIVATE_IP="$(get_private_ip)"
WEBUI_PASSWORD="$(read_webui_password)"

printf '\n%b%s%b\n' "${BOLD}${GREEN}" "$(t done)" "${RESET}"
printf '%-12s: %s\n' "$(t container)" "${CONTAINER}"
printf '%-12s: %s\n' "$(t source_dir)" "${APP_DIR}"
printf '%-12s: %s\n' "$(t webui_password)" "${WEBUI_PASSWORD}"
print_urls "$(t public_url)" "${PUBLIC_IP}"
print_urls "$(t private_url)" "${PRIVATE_IP}"
