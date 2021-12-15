#!/bin/sh

export DofusInvoker="/home/lucki/.config/Ankama/Dofus/DofusInvoker.swf"
export selectclass='com.ankamagames.dofus.BuildInfos,com.ankamagames.dofus.network.++,com.ankamagames.jerakine.network.++'
export config='parallelSpeedUp=0,exportTimeout=13000'

cd "$( dirname "${BASH_SOURCE[0]}" )"
cd ..

ffdec \
  -config "$config" \
    -selectclass "$selectclass" \
      -export script \
        ./sources $DofusInvoker
