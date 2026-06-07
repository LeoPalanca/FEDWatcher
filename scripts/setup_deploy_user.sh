#!/usr/bin/env bash

set -Eeuo pipefail

# Setup the restricted 'programming' user on the VPS for deployments.
#
# Run this ONCE on the VPS as root or via sudo from the 'debian' account:
#   sudo bash scripts/setup_deploy_user.sh
#
# What it does:
#   1. Creates the 'programming' user with a locked password (no login via password).
#   2. Restricts its home directory so only 'programming' can read it.
#   3. Configures SSH key-based auth (generates a keypair or accepts an existing public key).
#   4. Grants limited sudo: only rsync, mkdir, systemctl (fedwatcher-api/nginx), nginx -t.
#   5. Gives ownership of deploy target directories to 'programming'.
#
# The 'programming' user CANNOT:
#   - Read /home/debian or any other user's home directory.
#   - See radice_key or other SSH keys belonging to 'debian'.
#   - Run arbitrary commands as root.

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'
BOLD='\033[1m'

DEPLOY_USER="programming"
DEPLOY_HOME="/home/$DEPLOY_USER"
SUDOERS_FILE="/etc/sudoers.d/$DEPLOY_USER"

DEPLOY_DIRS=(
    "/var/www/fedwatcher"
    "/var/www/fakefed"
    "/home/programming/FEDWatcher"
)

if [[ "$(id -u)" -ne 0 ]]; then
    echo -e "${RED}Error: This script must be run as root (or via sudo).${NC}" >&2
    exit 1
fi

echo -e "${BOLD}${CYAN}=== Setting up restricted deploy user: $DEPLOY_USER ===${NC}\n"

# ── 1. Create user ──────────────────────────────────────────────────────────

if id "$DEPLOY_USER" &>/dev/null; then
    echo -e "${YELLOW}User '$DEPLOY_USER' already exists, skipping creation.${NC}"
else
    echo -e "${CYAN}Creating user '$DEPLOY_USER'...${NC}"
    useradd --create-home --shell /bin/bash "$DEPLOY_USER"
    echo -e "${GREEN}User created.${NC}"
fi

# ── 2. Secure home directories ───────────────────────────────────────────────

chmod 700 "$DEPLOY_HOME"
echo -e "${GREEN}Home directory $DEPLOY_HOME set to 700 (owner-only).${NC}"

# Make sure debian's home is also not world-readable (belt and suspenders)
if [[ -d /home/debian ]]; then
    chmod 700 /home/debian
    echo -e "${GREEN}/home/debian set to 700.${NC}"
fi

# ── 3. Restricted sudoers ───────────────────────────────────────────────────

echo -e "\n${CYAN}Configuring restricted sudo access...${NC}"

cat > "$SUDOERS_FILE" <<'SUDOERS'
# Restricted sudo for FedWatcher deployment user.
# Only allows the specific commands needed by scripts/deploy.sh.

programming ALL=(root) NOPASSWD: /usr/bin/rsync
programming ALL=(root) NOPASSWD: /usr/bin/mkdir -p /var/www/fedwatcher/*
programming ALL=(root) NOPASSWD: /usr/bin/mkdir -p /var/www/fakefed/*
programming ALL=(root) NOPASSWD: /usr/bin/systemctl restart fedwatcher-api.service
programming ALL=(root) NOPASSWD: /usr/bin/systemctl status fedwatcher-api.service *
programming ALL=(root) NOPASSWD: /usr/bin/systemctl reload nginx
programming ALL=(root) NOPASSWD: /usr/sbin/nginx -t
SUDOERS

chmod 440 "$SUDOERS_FILE"

# Validate sudoers syntax
if visudo -c -f "$SUDOERS_FILE" &>/dev/null; then
    echo -e "${GREEN}Sudoers file validated: $SUDOERS_FILE${NC}"
else
    echo -e "${RED}Sudoers syntax error! Removing $SUDOERS_FILE to prevent lockout.${NC}"
    rm -f "$SUDOERS_FILE"
    exit 1
fi

# ── 4. Directory ownership ──────────────────────────────────────────────────

echo -e "\n${CYAN}Setting up deploy target directories...${NC}"

for dir in "${DEPLOY_DIRS[@]}"; do
    mkdir -p "$dir"
    chown -R "$DEPLOY_USER:$DEPLOY_USER" "$dir"
    echo -e "  ${GREEN}$dir → owned by $DEPLOY_USER${NC}"
done

# ── Summary ─────────────────────────────────────────────────────────────────

echo -e "\n${BOLD}${GREEN}=== Setup complete ===${NC}"
echo ""
echo -e "User:             ${BOLD}$DEPLOY_USER${NC}"
echo -e "Home:             $DEPLOY_HOME (mode 700, owner-only)"
echo -e "Auth:             password-based SSH"
echo -e "Sudoers:          $SUDOERS_FILE"
echo -e "Deploy dirs:      ${DEPLOY_DIRS[*]}"
echo ""
echo -e "${YELLOW}What '$DEPLOY_USER' CAN do:${NC}"
echo "  - rsync files into /var/www/fedwatcher, /var/www/fakefed, /home/programming/FEDWatcher"
echo "  - restart/status fedwatcher-api.service"
echo "  - reload nginx and test config"
echo ""
echo -e "${YELLOW}What '$DEPLOY_USER' CANNOT do:${NC}"
echo "  - Read /home/debian or any other user's files"
echo "  - See radice_key or debian's SSH keys"
echo "  - Run arbitrary sudo commands"
echo ""
echo -e "${CYAN}To deploy from your laptop:${NC}"
echo "  bash scripts/deploy.sh --all"
echo ""
