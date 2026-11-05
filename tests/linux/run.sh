#!/bin/bash
# Runs inside the container, as a normal user, on a copy of the read-only /repo.
export XDG_RUNTIME_DIR=/tmp/run LANG=C.UTF-8
mkdir -p "$XDG_RUNTIME_DIR"; chmod 700 "$XDG_RUNTIME_DIR"
pulseaudio --start --exit-idle-time=-1 --log-target=stderr 2>/dev/null
sleep 1
pactl load-module module-null-sink sink_name=virtual >/dev/null
pactl set-default-sink virtual
cp -r /repo /tmp/work && cd /tmp/work || exit 1

echo "=== versions"; soffice --version; python3 --version; spd-say --version | head -1
echo "=== unit tests"; PYTHONWARNINGS=ignore python3 -m unittest discover -s tests 2>&1 | tail -4
echo "=== build"; python3 build.py
echo "=== speech dispatcher"; python3 tests/speech_dispatcher_check.py virtual.monitor

echo "=== libreoffice"
export LECTURE_VOCALE_HEADLESS=1 LECTURE_VOCALE_EXPORT_PATH=/tmp/export.wav LV_EXPECT_ENGINE=spd LV_VOICE_STEP=20 LV_EXPECT_AUTO_MESSAGE="Nothing to read"
PROFILE=file:///tmp/lo-profile
unopkg add -v "-env:UserInstallation=$PROFILE" dist/LectureVocale-*.oxt 2>&1 | tail -3
unopkg list "-env:UserInstallation=$PROFILE" 2>&1 | grep -iE 'Identifier|Version|Registered'
soffice "-env:UserInstallation=$PROFILE" --headless --norestore --nologo \
    "--accept=socket,host=localhost,port=2002;urp;" >/tmp/soffice.log 2>&1 &
python3 tests/libreoffice_check.py "$HOME/.config/lecture-vocale-libreoffice/lecture-vocale.log"
echo "exit $?"
echo "=== extension log"; tail -25 "$HOME/.config/lecture-vocale-libreoffice/lecture-vocale.log"
