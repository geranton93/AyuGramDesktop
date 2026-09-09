#!/usr/bin/env bash
set -e
FullExecPath=$PWD
pushd `dirname $0` > /dev/null
FullScriptPath=`pwd`
popd > /dev/null

Param1="$1"
Param2="$2"
Param3="$3"
Param4="$4"

if [ ! -d "$FullScriptPath/../../../DesktopPrivate" ]; then
  echo ""
  echo "This script is for building the production version of Telegram Desktop."
  echo ""
  echo "For building custom versions please visit the build instructions page at:"
  echo "https://github.com/telegramdesktop/tdesktop/#build-instructions"
  exit
fi

Error () {
  cd $FullExecPath
  echo "$1"
  exit 1
}

source "$FullScriptPath/version_parser.sh"
if ! read_version_file "$FullScriptPath/version"; then
	Error "Invalid version metadata."
fi

if [ "$AppVersion" -lt 7002000 ]; then
  Error "The v2 update format requires version 7.2 or newer."
fi

VersionForPacker="$AppVersion"
if [ "$AlphaVersion" != "0" ]; then
  Error "No releases for closed alpha versions"
elif [ "$BetaChannel" == "0" ]; then
  AppVersionStrFull="$AppVersionStr"
  AlphaBetaParam=''
else
  AppVersionStrFull="$AppVersionStr.beta"
  AlphaBetaParam='-beta'
fi

cd "$FullScriptPath"
python3 release.py $AppVersionStr $Param1 $Param2 $Param3 $Param4
