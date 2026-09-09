#!/usr/bin/env bash

# shellcheck disable=SC2034
read_version_file() {
	local version_file="$1"
	local key=""
	local value=""
	local extra=""
	local seen_AppVersion=0
	local seen_AppVersionStrMajor=0
	local seen_AppVersionStrSmall=0
	local seen_AppVersionStr=0
	local seen_BetaChannel=0
	local seen_AlphaVersion=0
	local seen_AppVersionOriginal=0

	while IFS=$' \t' read -r key value extra || [[ -n "$key" ]]; do
		if [[ -z "$key" ]]; then
			continue
		fi
		if [[ -n "$extra" ]]; then
			printf 'Invalid version file %s: too many fields on a line.\n' "$version_file" >&2
			return 1
		fi

		case "$key" in
			AppVersion|AppVersionStrMajor|AppVersionStrSmall|AppVersionStr|BetaChannel|AlphaVersion|AppVersionOriginal)
				;;
			*)
				printf 'Invalid version file %s: unknown key %s.\n' "$version_file" "$key" >&2
				return 1
		esac

		case "$key" in
			AppVersion)
				if ((seen_AppVersion != 0)) || [[ ! "$value" =~ ^[0-9]+$ ]]; then
					printf 'Invalid AppVersion in %s.\n' "$version_file" >&2
					return 1
				fi
				seen_AppVersion=1
				AppVersion="$value"
				;;
			AppVersionStrMajor)
				if ((seen_AppVersionStrMajor != 0)) || [[ ! "$value" =~ ^[0-9]+\.[0-9]+$ ]]; then
					printf 'Invalid AppVersionStrMajor in %s.\n' "$version_file" >&2
					return 1
				fi
				seen_AppVersionStrMajor=1
				AppVersionStrMajor="$value"
				;;
			AppVersionStrSmall)
				if ((seen_AppVersionStrSmall != 0)) || [[ ! "$value" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
					printf 'Invalid AppVersionStrSmall in %s.\n' "$version_file" >&2
					return 1
				fi
				seen_AppVersionStrSmall=1
				AppVersionStrSmall="$value"
				;;
			AppVersionStr)
				if ((seen_AppVersionStr != 0)) || [[ ! "$value" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
					printf 'Invalid AppVersionStr in %s.\n' "$version_file" >&2
					return 1
				fi
				seen_AppVersionStr=1
				AppVersionStr="$value"
				;;
			BetaChannel)
				if ((seen_BetaChannel != 0)) || [[ ! "$value" =~ ^[01]$ ]]; then
					printf 'Invalid BetaChannel in %s.\n' "$version_file" >&2
					return 1
				fi
				seen_BetaChannel=1
				BetaChannel="$value"
				;;
			AlphaVersion)
				if ((seen_AlphaVersion != 0)) || [[ ! "$value" =~ ^[0-9]+$ ]]; then
					printf 'Invalid AlphaVersion in %s.\n' "$version_file" >&2
					return 1
				fi
				seen_AlphaVersion=1
				AlphaVersion="$value"
				;;
			AppVersionOriginal)
				if ((seen_AppVersionOriginal != 0)) || [[ ! "$value" =~ ^[0-9]+\.[0-9]+\.[0-9]+(\.beta)?$ ]]; then
					printf 'Invalid AppVersionOriginal in %s.\n' "$version_file" >&2
					return 1
				fi
				seen_AppVersionOriginal=1
				AppVersionOriginal="$value"
				;;
		esac
	done < "$version_file"

	if ((seen_AppVersion == 0 || seen_AppVersionStrMajor == 0 || seen_AppVersionStrSmall == 0
		|| seen_AppVersionStr == 0 || seen_BetaChannel == 0 || seen_AlphaVersion == 0
		|| seen_AppVersionOriginal == 0)); then
		printf 'Invalid version file %s: a required key is missing.\n' "$version_file" >&2
		return 1
	fi
}
