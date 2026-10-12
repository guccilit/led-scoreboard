#!/bin/bash
# Build a working Ballin' Board from a freshly flashed card (milestone 9).
#
# 1. Flash Raspberry Pi OS Lite 64-bit with Raspberry Pi Imager. In Imager's settings:
#    hostname "scoreboard", user "dacino", the home WiFi, SSH on with the laptop's public key.
# 2. Put the card in the Pi, power up, wait a couple of minutes for it to join WiFi.
# 3. From the laptop (Git Bash, in the repo):   bash tools/deploy.sh
#    (copies the board program to ~/ballin-board on the Pi)
# 4. On the Pi:   sudo bash ~/ballin-board/setup/setup.sh [--restore /home/dacino/ballin-backup.tgz]
#    It runs in two stages with a reboot between them; run the same command again after the
#    reboot. --restore brings back settings, the alert-email password and saved WiFi from a
#    backup made with tools/backup.sh (only for replacing THIS board's card).
#
# Stage 1: packages, matrix library, sound off, core 3 for the panel, services,
#          the one-time card split (root 8 GiB + data partition "bbdata"), then reboot.
# Stage 2: data partition in use, optional restore, read-only system, then reboot.
# Safe to run again: each step checks whether it's already done.
set -euo pipefail

USER_NAME=dacino
HOME_DIR=/home/$USER_NAME
APP=$HOME_DIR/ballin-board
VENV=/opt/ballin-board/venv
MATRIX=$HOME_DIR/rpi-rgb-led-matrix
MATRIX_COMMIT=51d3231e370593b60952b2c3b18d2e3802329f18      # the version on the original board
BOOT=/boot/firmware
RESTORE=""
[ "${1:-}" = "--restore" ] && RESTORE="${2:-}"

say() { printf '\n== %s\n' "$*"; }
[ "$(id -u)" = 0 ] || { echo "Run with sudo: sudo bash $0"; exit 1; }
[ -d "$APP/ballin" ] || { echo "The board program isn't at $APP yet. From the laptop run: bash tools/deploy.sh"; exit 1; }
if grep -q "overlayroot=" /proc/cmdline; then
    echo "This card is already finished (read-only system active). Nothing to do."
    exit 0
fi

# ------------------------------------------------------------------ stage 2
if blkid -L bbdata >/dev/null 2>&1; then
    say "Stage 2: data partition found"
    sed -i 's/ bbsplit//' $BOOT/cmdline.txt                     # the split is done
    if [ -n "$RESTORE" ]; then
        [ -f "$RESTORE" ] || { echo "No backup at $RESTORE"; exit 1; }
        say "Restoring settings, secrets and WiFi from $RESTORE"
        systemctl stop ballin-board ballin-web 2>/dev/null || true
        # Only these, exactly where they belong (readonly.sh then copies the settings onto the
        # data partition, and the WiFi profiles become part of the read-only layer).
        tar -xzpf "$RESTORE" -C / var/lib/ballin-board etc/NetworkManager/system-connections
    fi
    say "Making the system read-only (safe to unplug at any time)"
    bash "$APP/setup/readonly.sh"
    say "Stage 2 done. Rebooting in 10 seconds; the board will come up finished."
    sleep 10
    reboot
    exit 0
fi

# ------------------------------------------------------------------ stage 1
say "Stage 1: packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y git build-essential cmake python3-dev python3-venv python3-pip cython3 \
    python3-pil python3-requests python3-setuptools libjpeg-dev zlib1g-dev \
    gpiod python3-libgpiod overlayroot curl avahi-daemon librsvg2-bin

say "Passwordless sudo for $USER_NAME (remote updates from the laptop)"
echo "$USER_NAME ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/010_$USER_NAME-nopasswd
chmod 440 /etc/sudoers.d/010_$USER_NAME-nopasswd

say "Onboard sound off (it disturbs the panel's timing)"
grep -q "^dtparam=audio=off" $BOOT/config.txt || sed -i 's/^dtparam=audio=on/dtparam=audio=off/' $BOOT/config.txt
grep -q "^dtparam=audio=off" $BOOT/config.txt || echo "dtparam=audio=off" >> $BOOT/config.txt
echo "blacklist snd_bcm2835" > /etc/modprobe.d/blacklist-rgb-matrix.conf

say "CPU core 3 reserved for the panel"
grep -q "isolcpus=3" $BOOT/cmdline.txt || sed -i '1 s/$/ isolcpus=3/' $BOOT/cmdline.txt

say "LED matrix library (hzeller rpi-rgb-led-matrix) into the app's Python"
if [ ! -d "$MATRIX/.git" ]; then
    sudo -u $USER_NAME git clone https://github.com/hzeller/rpi-rgb-led-matrix "$MATRIX"
fi
sudo -u $USER_NAME git -C "$MATRIX" fetch -q origin || true
sudo -u $USER_NAME git -C "$MATRIX" checkout -q "$MATRIX_COMMIT" || echo "(kept the current library version)"
[ -x "$VENV/bin/python" ] || python3 -m venv --system-site-packages "$VENV"
"$VENV/bin/pip" install --quiet "$MATRIX"
"$VENV/bin/python" -c "import rgbmatrix; print('matrix library ok')"

say "Board services"
cp "$APP/systemd/ballin-board.service" "$APP/systemd/ballin-web.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable ballin-board ballin-web

say "One-time card split on the next boot (root 8 GiB + data partition)"
install -m 755 "$APP/setup/initramfs/bb-split-hook" /etc/initramfs-tools/hooks/bb-split
install -m 755 "$APP/setup/initramfs/bb_split" /etc/initramfs-tools/scripts/local-premount/bb_split
update-initramfs -u
grep -q " bbsplit" $BOOT/cmdline.txt || sed -i '1 s/$/ bbsplit/' $BOOT/cmdline.txt

say "Stage 1 done. Rebooting in 10 seconds; the first boot splits the card (a minute or two)."
echo "When it's back, run the same command again:  sudo bash $APP/setup/setup.sh${RESTORE:+ --restore $RESTORE}"
sleep 10
reboot
