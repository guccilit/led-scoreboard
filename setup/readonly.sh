#!/bin/bash
# Make the board safe to unplug at any time (run as root; reboot afterwards).
#
#  * The data partition (label "bbdata", made by setup/initramfs/bb_split) is mounted at
#    /data and holds everything that must survive a restart: settings, caches, logos,
#    secrets (/var/lib/ballin-board) and the board program (/home/dacino/ballin-board).
#    Those paths are bind mounts of /data.
#  * The root filesystem becomes read-only (overlayroot: changes live in RAM and vanish
#    on restart). The boot partition is mounted read-only.
#  * Logs are kept in RAM; swap is compressed RAM only.
#
# Safe to run more than once. To change the system later (apt, pip): sudo overlayroot-chroot
set -euo pipefail

DATA=/data
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }
blkid -L bbdata >/dev/null || { echo "no partition labelled bbdata"; exit 1; }
if grep -q "overlayroot=" /proc/cmdline; then
    echo "already read-only (overlayroot active); use overlayroot-chroot to change the system"
    exit 1
fi

mkdir -p $DATA
grep -q "LABEL=bbdata" /etc/fstab || echo "LABEL=bbdata  $DATA  ext4  defaults,noatime  0  2" >> /etc/fstab
systemctl daemon-reload
mountpoint -q $DATA || mount $DATA

systemctl stop ballin-board ballin-web 2>/dev/null || true

persist() {          # persist <path on root> <dir on /data>
    local src=$1 dst=$2
    if [ ! -e "$dst/.ballin-persist" ]; then
        mkdir -p "$dst"
        [ -d "$src" ] && cp -a "$src/." "$dst/"
        touch "$dst/.ballin-persist"
        echo "copied $src -> $dst"
    fi
    mkdir -p "$src"
    grep -q " $src " /etc/fstab || \
        echo "$dst  $src  none  bind,x-systemd.requires-mounts-for=$DATA  0  0" >> /etc/fstab
}
persist /var/lib/ballin-board $DATA/ballin-board
persist /home/dacino/ballin-board $DATA/app
chown dacino:dacino $DATA/app

systemctl daemon-reload
mount -a
findmnt -rn -o TARGET,SOURCE | grep -E "^/(data|var/lib/ballin-board|home/dacino/ballin-board) "

# Logs in RAM only (capped).
mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nStorage=volatile\nRuntimeMaxUse=32M\n' > /etc/systemd/journald.conf.d/ballin.conf

# Swap: compressed RAM only (no swap file on the card).
if [ -f /etc/rpi/swap.conf ] && ! grep -q "^Mechanism=zram$" /etc/rpi/swap.conf; then
    sed -i 's/^\[Main\]$/[Main]\nMechanism=zram/' /etc/rpi/swap.conf
fi

# Boot partition read-only from the next boot.
sed -i 's#^\(PARTUUID=[^ ]*-01 *  */boot/firmware *vfat *\)defaults\( \)#\1defaults,ro\2#' /etc/fstab

# Root read-only (overlay in RAM); other mounts (/data) stay writable.
grep -q "overlayroot=" /boot/firmware/cmdline.txt || \
    sed -i '1 s/^/overlayroot=tmpfs:recurse=0 /' /boot/firmware/cmdline.txt

echo "--- fstab"; cat /etc/fstab
echo "--- cmdline"; cat /boot/firmware/cmdline.txt
echo "Done. Reboot to make the system read-only."
