# Build instructions for Windows 64-bit

- [Prepare folder](#prepare-folder)
- [Install third party software](#install-third-party-software)
- [Initialize terminal](#initialize-terminal)
- [Clone source code and prepare libraries](#clone-source-code-and-prepare-libraries)
- [Build the project](#build-the-project)
- [Troubleshooting](#troubleshooting)

## Prepare folder

The build is done in **Visual Studio 2026** with **10.0.26100.0** SDK version.

Make sure you have these components installed with Visual Studio:

- MSVC v143 - VS 2022 C++ x64/x86 build tools (v14.44-17.14)
- C++ MFC v14.44 (x86 & x64)
- C++ ATL v14.44 (x86 & x64)
- Windows 11 SDK (10.0.26100.0)

Choose an empty folder for the future build, for example **D:\\TBuild**. It will be named ***BuildPath*** in the rest of this document. Create two folders there, ***BuildPath*\\ThirdParty** and ***BuildPath*\\Libraries**.

## Install third party software

* Download **Python 3.10** installer from [https://www.python.org/downloads/](https://www.python.org/downloads/) and install it with adding to PATH.
* Download **Git** installer from [https://git-scm.com/download/win](https://git-scm.com/download/win) and install it.

## Initialize terminal

Before preparing libraries and running build commands, initialize the Visual Studio environment for your target architecture.
The default modern toolset from Visual Studio 2026 (`v145`) does not support Windows 7, so x86 and x64 builds must use `-vcvars_ver=14.44` (`v144.4`, based on `v143` with Windows 7 support).

For Visual Studio installation:

    %comspec% /k "C:\Program Files\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvars64.bat" -vcvars_ver=14.44

For Visual Studio Build Tools installation:

    %comspec% /k "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvars64.bat" -vcvars_ver=14.44

For `winarm` (ARM64, on an ARM64 host), initialize the toolset used for the application build, for example:

    %comspec% /k "C:\Program Files\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvarsall.bat" arm64 -vcvars_ver=14.52

MSVC 19.52 before 19.52.37025 generates incorrect ARM64 FFmpeg code ([issue #31570](https://github.com/telegramdesktop/tdesktop/issues/31570)). With those versions, dependency preparation automatically selects the 14.51 toolset for FFmpeg alone. Install the 14.51 ARM64 build tools in the same Visual Studio instance; MSVC 19.51.36256 has been verified. Other dependencies and the application keep the terminal's selected toolset. Fixed 19.52 versions build FFmpeg with the selected toolset as well.

Run dependency preparation and accept the FFmpeg rebuild, then rebuild AyuGram Desktop. The ARM64 FFmpeg cache includes the compiler actually used for FFmpeg, so an existing library built by the affected compiler is not reused automatically. Keep FFmpeg's default non-LTO compilation and use the newer toolset for the final application link when mixing compiler versions.

Run both `Clone source code and prepare libraries` and `Build the project` sections in the terminal initialized with one of the commands above.

## Clone source code and prepare libraries

In the initialized terminal, go to ***BuildPath*** and run

    git clone --recursive https://github.com/geranton93/AyuGramDesktop.git tdesktop
    tdesktop\Telegram\build\prepare\win.bat

## Build the project

Go to ***BuildPath*\\tdesktop\\Telegram** and run

    $env:TDESKTOP_API_ID = "YOUR_API_ID"
    $env:TDESKTOP_API_HASH = "YOUR_API_HASH"
    configure.bat x64 -D TDESKTOP_API_ID=$env:TDESKTOP_API_ID -D TDESKTOP_API_HASH=$env:TDESKTOP_API_HASH

For `winarm` (ARM64):

    configure.bat arm -T version=%VCToolsVersion% -D TDESKTOP_API_ID=YOUR_API_ID -D TDESKTOP_API_HASH=YOUR_API_HASH

* Open ***BuildPath*\\tdesktop\\out\\Telegram.slnx** in Visual Studio 2026
* Select Telegram project and press Build > Build Telegram (Debug and Release configurations)
* The result AyuGram.exe will be located in **D:\TBuild\tdesktop\out\Debug** (and **Release**)

## Troubleshooting

### Error building libvpx

Downgrade NASM in msys64 to 3.01.

### PDB API call failed

From the `tdesktop` folder, run the following command in PowerShell:

```powershell
@'
diff --git a/options_win.cmake b/options_win.cmake
index dd86342..857d5b7 100644
--- a/options_win.cmake
+++ b/options_win.cmake
@@ -34,2 +34,3 @@ if (MSVC)
         /MP     # Enable multi process build.
+        /FS
         /EHsc   # Catch C++ exceptions only, extern C functions never throw a C++ exception.
diff --git a/variables.cmake b/variables.cmake
index a1e77b7..9f816cb 100644
--- a/variables.cmake
+++ b/variables.cmake
@@ -28,4 +28,4 @@ set(CMAKE_CXX_SCAN_FOR_MODULES OFF CACHE BOOL "")
 set(CMAKE_MSVC_DEBUG_INFORMATION_FORMAT
-    "$<$<CONFIG:Debug>:ProgramDatabase>$<$<NOT:$<CONFIG:Debug>>:Embedded>"
-    CACHE STRING "")
+    "$<$<CONFIG:Debug,RelWithDebInfo>:ProgramDatabase>$<$<CONFIG:Release,MinSizeRel>:Embedded>"
+    CACHE STRING "" FORCE)
 set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>" CACHE STRING "")
'@ | git -C cmake apply -
```
