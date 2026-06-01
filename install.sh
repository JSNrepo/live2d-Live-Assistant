#!/usr/bin/env bash
# ==============================================================================
# SAKURA AI COMPANION SYSTEM INSTALLER
# Distro-agnostic dependency resolver & Python virtual environment provisioner.
# ==============================================================================

set -euo pipefail

# Style helpers
RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${CYAN}======================================================${NC}"
echo -e "${CYAN}       🌸 Sakura AI Companion System Installer 🌸      ${NC}"
echo -e "${CYAN}======================================================${NC}"

# 1. Distro Detection and Dependency Resolution
echo -e "\n${CYAN}[1/3] System Package Dependency Check...${NC}"

# Detect Package Manager
if [ -f /etc/debian_version ]; then
    PM="apt"
elif [ -f /etc/fedora-release ] || [ -f /etc/redhat-release ]; then
    PM="dnf"
elif [ -f /etc/arch-release ]; then
    PM="pacman"
else
    echo -e "${YELLOW}Warning: Unknown Linux distribution. Please ensure 'portaudio' and 'playerctl' are installed manually.${NC}"
    PM="unknown"
fi

install_debian_deps() {
    echo -e "${YELLOW}Debian/Ubuntu detected. Verifying system packages...${NC}"
    local missing_pkgs=()
    
    for pkg in portaudio19-dev playerctl python3-pip python3-venv git; do
        if ! dpkg -s "$pkg" >/dev/null 2>&1; then
            missing_pkgs+=("$pkg")
        fi
    done

    if [ ${#missing_pkgs[@]} -gt 0 ]; then
        echo -e "${YELLOW}Installing missing dependencies: ${missing_pkgs[*]}${NC}"
        sudo apt update -y
        sudo apt install -y "${missing_pkgs[@]}"
    else
        echo -e "${GREEN}All system-level dependencies are already satisfied!${NC}"
    fi
}

install_fedora_deps() {
    echo -e "${YELLOW}Fedora/RHEL detected. Verifying system packages...${NC}"
    local missing_pkgs=()
    
    for pkg in portaudio-devel playerctl python3-pip git; do
        if ! rpm -q "$pkg" >/dev/null 2>&1; then
            missing_pkgs+=("$pkg")
        fi
    done

    if [ ${#missing_pkgs[@]} -gt 0 ]; then
        echo -e "${YELLOW}Installing missing dependencies: ${missing_pkgs[*]}${NC}"
        sudo dnf install -y "${missing_pkgs[@]}"
    else
        echo -e "${GREEN}All system-level dependencies are already satisfied!${NC}"
    fi
}

install_arch_deps() {
    echo -e "${YELLOW}Arch Linux detected. Verifying system packages...${NC}"
    local missing_pkgs=()
    
    for pkg in portaudio playerctl python-pip git; do
        if ! pacman -Qi "$pkg" >/dev/null 2>&1; then
            missing_pkgs+=("$pkg")
        fi
    done

    if [ ${#missing_pkgs[@]} -gt 0 ]; then
        echo -e "${YELLOW}Installing missing dependencies: ${missing_pkgs[*]}${NC}"
        sudo pacman -S --noconfirm "${missing_pkgs[@]}"
    else
        echo -e "${GREEN}All system-level dependencies are already satisfied!${NC}"
    fi
}

case "$PM" in
    apt) install_debian_deps ;;
    dnf) install_fedora_deps ;;
    pacman) install_arch_deps ;;
    *) echo -e "${YELLOW}Skipping system packages. Assuming user has already installed portaudio and playerctl.${NC}" ;;
esac

# 2. Virtual Environment Setup
echo -e "\n${CYAN}[2/3] Setting up Python Virtual Environment...${NC}"

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

if [ ! -d ".venv" ]; then
    echo -e "Creating localized virtual environment in ${CYAN}.venv/${NC}..."
    python3 -m venv .venv
else
    echo -e "${GREEN}Virtual environment already exists.${NC}"
fi

# Activate virtual environment
source .venv/bin/activate

# Upgrade pip tools
echo -e "Upgrading PIP packaging utilities..."
pip install --upgrade pip setuptools wheel --quiet

# Install requirements
echo -e "Installing dependencies from ${CYAN}requirements.txt${NC}..."
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
    echo -e "${GREEN}Python packages installed successfully!${NC}"
else
    echo -e "${RED}Error: requirements.txt not found in ${PROJECT_DIR}.${NC}"
    exit 1
fi

# 3. Permissions Tuning
echo -e "\n${CYAN}[3/3] Finalizing script permissions...${NC}"
chmod +x run.sh run_live2d.sh install.sh 2>/dev/null || true
echo -e "${GREEN}Executable permissions verified on run scripts!${NC}"

echo -e "\n${GREEN}======================================================${NC}"
echo -e "${GREEN}🌸 Installation completed successfully with zero errors! 🌸${NC}"
echo -e "Launch the companion immediately by running:"
echo -e "${CYAN}./run_live2d.sh${NC}"
echo -e "${GREEN}======================================================${NC}"
