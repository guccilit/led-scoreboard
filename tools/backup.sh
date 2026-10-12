#!/bin/bash
# Back up the board from the laptop, without taking the SD card out (Git Bash, in the repo).
#
#   bash tools/backup.sh          settings backup (small, a few seconds): settings, the
#                                 alert-email password and saved WiFi.
#                                 Restore it onto a fresh card with setup/setup.sh --restore.
#   Run it on the same WiFi as the board.
#   bash tools/backup.sh --image  ALSO a full copy of the whole card (gzip), flashable with
#                                 Raspberry Pi Imager ("Use custom"). Takes a while; the board
#                                 keeps running (the system part is read-only, so the copy is
#                                 consistent).
#
# Files go to backups/ (not in git: they contain WiFi and email passwords). Keep a copy
# somewhere safe too.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups
STAMP=$(date +%Y-%m-%d_%H%M)

HOST=""
for h in scoreboard.local scoreboard-ip; do
    if ssh -o ConnectTimeout=6 -o BatchMode=yes "$h" true 2>/dev/null; then HOST=$h; break; fi
done
[ -n "$HOST" ] || { echo "Can't reach the board on the local network (tried scoreboard.local, scoreboard-ip)"; exit 1; }
echo "Board reachable as $HOST"

OUT=backups/ballin-backup-$STAMP.tgz
# WiFi profiles: the saved ones live in the read-only layer (/media/root-ro) once persisted.
ssh "$HOST" 'sudo tar -czf - -C / var/lib/ballin-board \
    $( [ -d /media/root-ro/etc/NetworkManager/system-connections ] && echo "-C /media/root-ro" ) \
    etc/NetworkManager/system-connections' > "$OUT"
echo "Settings backup: $OUT ($(du -h "$OUT" | cut -f1))"

if [ "${1:-}" = "--image" ]; then
    IMG=backups/ballin-card-$STAMP.img.gz
    echo "Copying the card to $IMG (only the parts in use; a few minutes up to half an hour)..."
    # A plain dd of all 32 GB took hours and made the panel flicker (2026-10-03). Instead: the
    # boot area as is, then each ext4 partition with e2image -ra, which reads only the blocks
    # in use and writes zeros for the rest (they compress to nothing). Same layout as the card,
    # so Raspberry Pi Imager can still write it.
    ssh "$HOST" 'sudo nice -n 19 ionice -c3 bash -c '"'"'
        set -e
        part() { cat /sys/block/mmcblk0/mmcblk0p$1/$2; }
        P2=$(part 2 start); P3=$(part 3 start)
        {
            dd if=/dev/mmcblk0 bs=512 count=$P2 status=none
            { /usr/sbin/e2image -ra /dev/mmcblk0p2 - 2>/dev/null; cat /dev/zero; } | head -c $(( $(part 2 size) * 512 ))
            { /usr/sbin/e2image -raf /dev/mmcblk0p3 - 2>/dev/null; cat /dev/zero; } | head -c $(( $(part 3 size) * 512 ))
        } | nice -n 19 gzip -1
    '"'"'' > "$IMG"
    echo "Card image: $IMG ($(du -h "$IMG" | cut -f1))"
    echo "To use it: Raspberry Pi Imager > Choose OS > Use custom > this file > write to a 32 GB card or larger."
fi
