#!/bin/sh
# Ensure /tmp/storage/recorder is a valid directory.
#
# BlueOS sometimes leaves /usr/blueos/userdata/recorder as a plain
# file (e.g. after a failed migration), which used to block the
# container from starting when it was a direct bind mount.  Now we
# mount the parent (userdata/) and fix the child here.

USERDATA="/tmp/storage/userdata"
RECORDER="$USERDATA/recorder"
LINK="/tmp/storage/recorder"

# Fix recorder if it exists as a file instead of a directory
if [ -e "$RECORDER" ] && [ ! -d "$RECORDER" ]; then
    echo "ENTRYPOINT: $RECORDER is not a directory, removing and recreating"
    rm -f "$RECORDER"
fi
mkdir -p "$RECORDER"

# Symlink so the rest of the app can use /tmp/storage/recorder
if [ -L "$LINK" ]; then
    rm -f "$LINK"
fi
if [ ! -e "$LINK" ]; then
    ln -sf "$RECORDER" "$LINK"
fi

exec python -m doris.main "$@"
