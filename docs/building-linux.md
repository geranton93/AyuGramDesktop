## Build instructions for Linux using Docker

### Prepare folder

Choose a folder for the future build, for example **/home/user/TBuild**. It will be named ***BuildPath*** in the rest of this document. All commands will be launched from Terminal.

### Clone source code and select a build image

Install [Docker](https://www.docker.com/) and [Buildx](https://docs.docker.com/reference/cli/docker/buildx/), go to ***BuildPath*** and clone the source.

    git clone --recursive https://github.com/geranton93/AyuGramDesktop.git tdesktop

To use the prebuilt Telegram Desktop image, set the image name as follows. Docker pulls it when needed.

    AYUGRAM_BUILD_IMAGE=ghcr.io/telegramdesktop/tdesktop/centos_env:latest

To build the image locally, install [Poetry](https://python-poetry.org) and run the following commands from ***BuildPath***.

    ./tdesktop/Telegram/build/prepare/linux.sh
    AYUGRAM_BUILD_IMAGE=tdesktop:centos_env

### Building the project

Go to ***BuildPath*/tdesktop** and run

    export TDESKTOP_API_ID=YOUR_API_ID
    export TDESKTOP_API_HASH=YOUR_API_HASH

    docker run --rm -it \
        -u $(id -u) \
        -v "$PWD:/usr/src/tdesktop" \
        "${AYUGRAM_BUILD_IMAGE:?Select a build image first}" \
        /usr/src/tdesktop/Telegram/build/docker/centos_env/build.sh \
        -D TDESKTOP_API_ID="${TDESKTOP_API_ID:?Set TDESKTOP_API_ID first}" \
        -D TDESKTOP_API_HASH="${TDESKTOP_API_HASH:?Set TDESKTOP_API_HASH first}"

Or, to create a debug build, run

    docker run --rm -it \
        -u $(id -u) \
        -v "$PWD:/usr/src/tdesktop" \
        -e CONFIG=Debug \
        "${AYUGRAM_BUILD_IMAGE:?Select a build image first}" \
        /usr/src/tdesktop/Telegram/build/docker/centos_env/build.sh \
        -D TDESKTOP_API_ID="${TDESKTOP_API_ID:?Set TDESKTOP_API_ID first}" \
        -D TDESKTOP_API_HASH="${TDESKTOP_API_HASH:?Set TDESKTOP_API_HASH first}"

The built files will be in the `out` directory.

You can use `strip` command to reduce binary size.

### Visual Studio Code integration

Follow the local-image route in [**Clone source code and select a build image**](#clone-source-code-and-select-a-build-image). Dev Containers uses the `tdesktop:centos_env` tag created by that route.

Open the repository in Visual Studio Code, install the [Dev Containers](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers) extension and add the following to `.vscode/settings.json` (using [your **api_id** and **api_hash**](#obtain-your-api-credentials)):

    {
        "cmake.configureSettings": {
            "TDESKTOP_API_ID": "YOUR_API_ID",
            "TDESKTOP_API_HASH": "YOUR_API_HASH"
        }
    }

After that, choose **Reopen in Container** via the menu triggered by the green button in bottom left corner and you're done.

![Quick actions Status bar item](https://code.visualstudio.com/assets/docs/devcontainers/containers/remote-dev-status-bar.png)
