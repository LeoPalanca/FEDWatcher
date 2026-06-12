#!/usr/bin/env bash

set -Eeuo pipefail

# FedWatcher Deployment Script
# Deploys frontend, backend, or FakeFed either over SSH or directly on the VPS.

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'
BOLD='\033[1m'

DEFAULT_HOST="fedwatcher.ellep.it"
DEFAULT_USER="programming"
DEFAULT_FRONTEND_DEST="/var/www/fedwatcher"
DEFAULT_BACKEND_DEST="/home/programming/FEDWatcher"
DEFAULT_FAKEFED_DEST="/var/www/fakefed"
BACKEND_SERVICE="fedwatcher-api.service"
GITHUB_REPO="https://github.com/LeoPalanca/FEDWatcher.git"

HOST="$DEFAULT_HOST"
REMOTE_USER="$DEFAULT_USER"
SSH_KEY=""
DRY_RUN=false
DEPLOY_FRONTEND=false
DEPLOY_BACKEND=false
DEPLOY_FAKEFED=false
RESTART_BACKEND=false
RELOAD_NGINX=false
AUTO_YES=false
LOCAL_MODE=false
DEPLOY_SOURCE=""
TEMP_DIR=""

FRONTEND_EXCLUDES=(".DS_Store" "proposal/")
FAKEFED_EXCLUDES=(".DS_Store")
BACKEND_EXCLUDES=(
    ".git/"
    ".env"
    ".env.local"
    ".env.production"
    "*.db"
    "*.sqlite"
    "*.sqlite3"
    ".venv/"
    "venv/"
    "logs/"
    "__pycache__/"
    ".pytest_cache/"
    ".mypy_cache/"
    ".ruff_cache/"
    ".DS_Store"
    "fedwatcher/"
    "fakefed/"
    "academic_doc/"
)

show_help() {
    cat <<EOF
Usage: $0 [options]

Options:
  --host <host>          VPS hostname/IP (default: $DEFAULT_HOST)
  --user <user>          SSH user (default: $DEFAULT_USER)
  -i <key_file>          Path to custom SSH private key
  --local                Deploy directly on the VPS filesystem, no SSH
  --source <local|github> Deploy from local working tree or clone from GitHub
  --frontend             Deploy the static frontend website only
  --backend              Deploy the backend Python/FastAPI code only
  --fakefed              Deploy the FakeFed static test fixtures only
  --all                  Deploy frontend, backend, and FakeFed
  --restart              Restart $BACKEND_SERVICE after deploy
  --reload-nginx         Reload Nginx after deploy
  --dry-run              Show files and commands without changing the server
  -y, --yes              Skip confirmation prompts
  -h, --help             Show this help screen

Examples:
  $0 --all --local --restart --reload-nginx
  $0 --frontend --reload-nginx --source github
  $0 --backend --restart --host 203.0.113.10 --user debian -i ~/.ssh/fedwatcher
EOF
}

fail() {
    echo -e "${RED}Error: $*${NC}" >&2
    exit 1
}

require_value() {
    local option="$1"
    local value="${2:-}"

    if [[ -z "$value" || "$value" == --* ]]; then
        fail "$option requires a value."
    fi
}

print_header() {
    echo -e "${BOLD}${BLUE}===============================================${NC}"
    echo -e "${BOLD}${BLUE}   FedWatcher VPS Deployer                     ${NC}"
    echo -e "${BOLD}${BLUE}===============================================${NC}"
}

parse_args() {
    while [[ "$#" -gt 0 ]]; do
        case "$1" in
            --host)
                require_value "$1" "${2:-}"
                HOST="$2"
                shift 2
                ;;
            --user)
                require_value "$1" "${2:-}"
                REMOTE_USER="$2"
                shift 2
                ;;
            -i)
                require_value "$1" "${2:-}"
                SSH_KEY="$2"
                shift 2
                ;;
            --local)
                LOCAL_MODE=true
                shift
                ;;
            --source)
                require_value "$1" "${2:-}"
                DEPLOY_SOURCE="$2"
                if [[ "$DEPLOY_SOURCE" != "local" && "$DEPLOY_SOURCE" != "github" ]]; then
                    fail "--source must be 'local' or 'github'."
                fi
                shift 2
                ;;
            --frontend)
                DEPLOY_FRONTEND=true
                shift
                ;;
            --backend)
                DEPLOY_BACKEND=true
                shift
                ;;
            --fakefed)
                DEPLOY_FAKEFED=true
                shift
                ;;
            --all)
                DEPLOY_FRONTEND=true
                DEPLOY_BACKEND=true
                DEPLOY_FAKEFED=true
                shift
                ;;
            --restart)
                RESTART_BACKEND=true
                shift
                ;;
            --reload-nginx)
                RELOAD_NGINX=true
                shift
                ;;
            --dry-run)
                DRY_RUN=true
                shift
                ;;
            -y|--yes)
                AUTO_YES=true
                shift
                ;;
            -h|--help)
                show_help
                exit 0
                ;;
            *)
                show_help
                fail "Unknown parameter: $1"
                ;;
        esac
    done
}

choose_targets_if_needed() {
    if [[ "$DEPLOY_FRONTEND" == true || "$DEPLOY_BACKEND" == true || "$DEPLOY_FAKEFED" == true ]]; then
        return
    fi

    if [[ ! -t 0 ]]; then
        fail "No deployment target specified. Run with -h for help."
    fi

    echo -e "${YELLOW}No deployment targets specified. Please choose what to deploy:${NC}"
    echo "1) Frontend Website (to $DEFAULT_FRONTEND_DEST)"
    echo "2) Backend API Services (to $DEFAULT_BACKEND_DEST)"
    echo "3) FakeFed Static Fixtures (to $DEFAULT_FAKEFED_DEST)"
    echo "4) Deploy All"
    echo "5) Exit"
    read -r -p "Select choice [1-5]: " choice

    case "$choice" in
        1) DEPLOY_FRONTEND=true; RELOAD_NGINX=true ;;
        2) DEPLOY_BACKEND=true; RESTART_BACKEND=true ;;
        3) DEPLOY_FAKEFED=true; RELOAD_NGINX=true ;;
        4)
            DEPLOY_FRONTEND=true
            DEPLOY_BACKEND=true
            DEPLOY_FAKEFED=true
            RESTART_BACKEND=true
            RELOAD_NGINX=true
            ;;
        *) echo "Deployment cancelled."; exit 0 ;;
    esac
}

choose_source_if_needed() {
    if [[ -n "$DEPLOY_SOURCE" ]]; then
        return
    fi

    if [[ ! -t 0 ]]; then
        DEPLOY_SOURCE="local"
        return
    fi

    echo -e "\n${YELLOW}Where do you want to deploy from?${NC}"
    echo "1) Local working tree (current repo)"
    echo "2) GitHub (fresh clone from $GITHUB_REPO)"
    read -r -p "Select choice [1-2]: " src_choice

    case "$src_choice" in
        1) DEPLOY_SOURCE="local" ;;
        2) DEPLOY_SOURCE="github" ;;
        *) echo "Defaulting to local."; DEPLOY_SOURCE="local" ;;
    esac
}

clone_from_github() {
    TEMP_DIR="$(mktemp -d)"
    echo -e "${CYAN}Cloning ${GITHUB_REPO} into temporary directory...${NC}"
    git clone --depth 1 "$GITHUB_REPO" "$TEMP_DIR" || fail "Failed to clone from GitHub."
    echo -e "${GREEN}Clone complete.${NC}"
}

build_ssh_args() {
    SSH_CONTROL_SOCKET="/tmp/fedwatcher-ssh-$$"
    SSH_ARGS=(
        -o ConnectTimeout=5
        -o ControlMaster=auto
        -o ControlPath="$SSH_CONTROL_SOCKET"
        -o ControlPersist=120
    )
    if [[ -n "$SSH_KEY" ]]; then
        [[ -f "$SSH_KEY" ]] || fail "SSH key does not exist: $SSH_KEY"
        SSH_ARGS+=(-i "$SSH_KEY")
    fi
}

print_cmd() {
    printf '%q ' "$@"
    printf '\n'
}

run_remote() {
    if [[ "$DRY_RUN" == true ]]; then
        echo -n "[DRY RUN] "
        print_cmd ssh "${SSH_ARGS[@]}" "${REMOTE_USER}@${HOST}" "$@"
        return
    fi

    ssh "${SSH_ARGS[@]}" "${REMOTE_USER}@${HOST}" "$@"
}

run_sudo() {
    if [[ "$DRY_RUN" == true ]]; then
        echo -n "[DRY RUN] "
        if [[ "$LOCAL_MODE" == true ]]; then
            print_cmd sudo "$@"
        else
            print_cmd ssh "${SSH_ARGS[@]}" "${REMOTE_USER}@${HOST}" sudo "$@"
        fi
        return
    fi

    if [[ "$LOCAL_MODE" == true ]]; then
        sudo "$@"
    else
        run_remote sudo "$@"
    fi
}

test_connection() {
    if [[ "$LOCAL_MODE" == true ]]; then
        echo -e "${GREEN}Running in LOCAL mode on the VPS filesystem.${NC}"
        return
    fi

    if [[ "$DRY_RUN" == true ]]; then
        echo -e "${YELLOW}[DRY RUN] Skipping SSH connection test for ${REMOTE_USER}@${HOST}.${NC}"
        return
    fi

    echo -e "${CYAN}Testing SSH connection to ${REMOTE_USER}@${HOST}...${NC}"
    if ! ssh "${SSH_ARGS[@]}" "${REMOTE_USER}@${HOST}" true >/dev/null 2>&1; then
        fail "Cannot connect to ${REMOTE_USER}@${HOST} via SSH."
    fi
    echo -e "${GREEN}Connection verified.${NC}"
}

ensure_dir() {
    local dest="$1"
    local use_sudo="$2"

    if [[ "$use_sudo" == true ]]; then
        run_sudo mkdir -p "$dest"
    elif [[ "$DRY_RUN" == true ]]; then
        echo -n "[DRY RUN] "
        if [[ "$LOCAL_MODE" == true ]]; then
            print_cmd mkdir -p "$dest"
        else
            print_cmd ssh "${SSH_ARGS[@]}" "${REMOTE_USER}@${HOST}" mkdir -p "$dest"
        fi
    elif [[ "$LOCAL_MODE" == true ]]; then
        mkdir -p "$dest"
    else
        run_remote mkdir -p "$dest"
    fi
}

deploy_dir() {
    local src="$1"
    local dest="$2"
    local use_sudo="$3"
    shift 3
    local excludes=("$@")
    local -a rsync_cmd

    [[ -d "$src" ]] || fail "Source directory does not exist: $src"
    ensure_dir "$dest" "$use_sudo"

    if [[ "$LOCAL_MODE" == true ]]; then
        echo -e "${CYAN}Syncing ${src} to ${dest}...${NC}"
        if [[ "$use_sudo" == true ]]; then
            rsync_cmd=(sudo rsync -rzltD --delete)
        else
            rsync_cmd=(rsync -rzltD --delete)
        fi
    else
        echo -e "${CYAN}Deploying ${src} to ${REMOTE_USER}@${HOST}:${dest}...${NC}"
        rsync_cmd=(rsync -rzltD --delete)
        if [[ "$use_sudo" == true ]]; then
            rsync_cmd+=(--rsync-path "sudo rsync")
        fi
        local ssh_transport="ssh -o ConnectTimeout=5 -o ControlMaster=auto -o ControlPath=$SSH_CONTROL_SOCKET -o ControlPersist=120"
        if [[ -n "$SSH_KEY" ]]; then
            ssh_transport+=" -i $SSH_KEY"
        fi
        rsync_cmd+=(-e "$ssh_transport")
    fi

    if [[ "$DRY_RUN" == true ]]; then
        rsync_cmd+=(--dry-run --itemize-changes)
    fi

    for exc in "${excludes[@]}"; do
        rsync_cmd+=(--exclude "$exc")
    done

    if [[ "$LOCAL_MODE" == true ]]; then
        rsync_cmd+=("$src" "$dest")
    else
        rsync_cmd+=("$src" "${REMOTE_USER}@${HOST}:${dest}")
    fi

    printf '%b' "$CYAN"
    print_cmd "${rsync_cmd[@]}"
    printf '%b' "$NC"

    if [[ "$DRY_RUN" == false ]]; then
        "${rsync_cmd[@]}"
    fi
}

confirm_plan() {
    if [[ "$AUTO_YES" == true || "$DRY_RUN" == true ]]; then
        return
    fi

    echo -e "\n${BOLD}${YELLOW}Summary of planned actions:${NC}"
    if [[ "$LOCAL_MODE" == true ]]; then
        [[ "$DEPLOY_FRONTEND" == true ]] && echo -e "  - Deploy frontend website from ${BLUE}fedwatcher/${NC} to ${BLUE}${DEFAULT_FRONTEND_DEST}${NC}"
        [[ "$DEPLOY_BACKEND" == true ]] && echo -e "  - Deploy backend API from ${BLUE}./${NC} to ${BLUE}${DEFAULT_BACKEND_DEST}${NC}"
        [[ "$DEPLOY_FAKEFED" == true ]] && echo -e "  - Deploy FakeFed fixtures from ${BLUE}fakefed/${NC} to ${BLUE}${DEFAULT_FAKEFED_DEST}${NC}"
    else
        [[ "$DEPLOY_FRONTEND" == true ]] && echo -e "  - Deploy frontend website from ${BLUE}fedwatcher/${NC} to remote ${BLUE}${HOST}:${DEFAULT_FRONTEND_DEST}${NC}"
        [[ "$DEPLOY_BACKEND" == true ]] && echo -e "  - Deploy backend API from ${BLUE}./${NC} to remote ${BLUE}${HOST}:${DEFAULT_BACKEND_DEST}${NC}"
        [[ "$DEPLOY_FAKEFED" == true ]] && echo -e "  - Deploy FakeFed fixtures from ${BLUE}fakefed/${NC} to remote ${BLUE}${HOST}:${DEFAULT_FAKEFED_DEST}${NC}"
    fi
    [[ "$RESTART_BACKEND" == true ]] && echo -e "  - Restart systemd service ${BLUE}${BACKEND_SERVICE}${NC}"
    [[ "$RELOAD_NGINX" == true ]] && echo -e "  - Validate and reload ${BLUE}nginx${NC}"

    read -r -p "Do you want to proceed? [y/N] " confirm
    if [[ ! "$confirm" =~ ^[yY]([eE][sS])?$ ]]; then
        echo "Deployment cancelled."
        exit 0
    fi
}

cleanup() {
    # Close the SSH multiplexed connection if open
    if [[ -n "${SSH_CONTROL_SOCKET:-}" && -S "$SSH_CONTROL_SOCKET" ]]; then
        ssh -o ControlPath="$SSH_CONTROL_SOCKET" -O exit "${REMOTE_USER}@${HOST}" 2>/dev/null || true
    fi
    if [[ -n "$TEMP_DIR" && -d "$TEMP_DIR" ]]; then
        echo -e "${CYAN}Cleaning up temporary directory...${NC}"
        rm -rf "$TEMP_DIR"
    fi
}

main() {
    trap cleanup EXIT
    print_header
    parse_args "$@"
    choose_targets_if_needed
    choose_source_if_needed

    if [[ "$DEPLOY_SOURCE" == "github" ]]; then
        clone_from_github
        REPO_ROOT="$TEMP_DIR"
        echo -e "${GREEN}Deploying from GitHub clone.${NC}"
    else
        REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
        echo -e "${GREEN}Deploying from local working tree.${NC}"
    fi
    cd "$REPO_ROOT"

    build_ssh_args
    test_connection
    confirm_plan

    if [[ "$DEPLOY_FRONTEND" == true ]]; then
        echo -e "\n${BOLD}=== Deploying Frontend ===${NC}"
        deploy_dir "fedwatcher/" "$DEFAULT_FRONTEND_DEST/" true "${FRONTEND_EXCLUDES[@]}"
    fi

    if [[ "$DEPLOY_BACKEND" == true ]]; then
        echo -e "\n${BOLD}=== Deploying Backend ===${NC}"
        local backend_sudo=false
        if [[ "$REMOTE_USER" != "programming" ]]; then
            backend_sudo=true
        fi
        deploy_dir "./" "$DEFAULT_BACKEND_DEST/" "$backend_sudo" "${BACKEND_EXCLUDES[@]}"
        if [[ "$backend_sudo" == true ]]; then
            run_sudo chown -R programming:programming "$DEFAULT_BACKEND_DEST"
        fi
    fi

    if [[ "$DEPLOY_FAKEFED" == true ]]; then
        echo -e "\n${BOLD}=== Deploying FakeFed ===${NC}"
        deploy_dir "fakefed/" "$DEFAULT_FAKEFED_DEST/" true "${FAKEFED_EXCLUDES[@]}"
        run_sudo chown -R programming:programming "$DEFAULT_FAKEFED_DEST"
    fi

    if [[ "$RESTART_BACKEND" == true ]]; then
        echo -e "\n${BOLD}=== Restarting Backend Service ===${NC}"
        run_sudo systemctl restart "$BACKEND_SERVICE"
        run_sudo systemctl status "$BACKEND_SERVICE" --no-pager -n 5
    fi

    if [[ "$RELOAD_NGINX" == true ]]; then
        echo -e "\n${BOLD}=== Reloading Nginx ===${NC}"
        run_sudo nginx -t
        run_sudo systemctl reload nginx
    fi

    echo -e "\n${BOLD}${GREEN}Deployment completed successfully.${NC}\n"
}

main "$@"
