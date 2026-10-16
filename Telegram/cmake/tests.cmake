# This file is part of Telegram Desktop,
# the official desktop application for the Telegram messaging service.
#
# For license and copyright information please follow this link:
# https://github.com/telegramdesktop/tdesktop/blob/master/LEGAL

add_executable(test_text WIN32)
init_target(test_text "(tests)")

target_include_directories(test_text PRIVATE ${src_loc})

nice_target_sources(test_text ${src_loc}
PRIVATE
    tests/test_main.cpp
    tests/test_main.h
    tests/test_text.cpp
)

nice_target_sources(test_text ${res_loc}
PRIVATE
    qrc/emoji_1.qrc
    qrc/emoji_2.qrc
    qrc/emoji_3.qrc
    qrc/emoji_4.qrc
    qrc/emoji_5.qrc
    qrc/emoji_6.qrc
    qrc/emoji_7.qrc
    qrc/emoji_8.qrc
)

target_link_libraries(test_text
PRIVATE
    desktop-app::lib_base
    desktop-app::lib_crl
    desktop-app::lib_ui
    desktop-app::external_qt
    desktop-app::external_qt_static_plugins
)

set_target_properties(test_text PROPERTIES RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR})

add_dependencies(Telegram test_text)

target_prepare_qrc(test_text)

if (APPLE)
    add_custom_command(TARGET test_text POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E make_directory
            "$<TARGET_FILE_DIR:test_text>/Contents/Resources"
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
            "${CMAKE_BINARY_DIR}/test_text.rcc"
            "${CMAKE_BINARY_DIR}/lib_ui.rcc"
            "$<TARGET_FILE_DIR:test_text>/Contents/Resources/"
    )
endif()

add_executable(test_data_unsent_read_generation)
init_target(test_data_unsent_read_generation "(tests)")

target_include_directories(test_data_unsent_read_generation PRIVATE ${src_loc})

nice_target_sources(test_data_unsent_read_generation ${src_loc}
PRIVATE
    tests/data_unsent_read_generation_test.cpp
)

set_target_properties(test_data_unsent_read_generation PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}
)

add_dependencies(Telegram test_data_unsent_read_generation)

add_executable(test_data_unsent_read_till)
init_target(test_data_unsent_read_till "(tests)")

target_include_directories(test_data_unsent_read_till PRIVATE ${src_loc})

nice_target_sources(test_data_unsent_read_till ${src_loc}
PRIVATE
    tests/data_unsent_read_till_test.cpp
)

set_target_properties(test_data_unsent_read_till PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}
)

add_dependencies(Telegram test_data_unsent_read_till)

add_executable(test_ayu_ghost_mode_peer_exceptions)
init_target(test_ayu_ghost_mode_peer_exceptions "(tests)")

target_include_directories(test_ayu_ghost_mode_peer_exceptions PRIVATE ${src_loc})

nice_target_sources(test_ayu_ghost_mode_peer_exceptions ${src_loc}
PRIVATE
    tests/ayu_ghost_mode_peer_exceptions_test.cpp
)

set_target_properties(test_ayu_ghost_mode_peer_exceptions PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}
)

add_dependencies(Telegram test_ayu_ghost_mode_peer_exceptions)

add_executable(test_filter_cache)
init_target(test_filter_cache "(tests)")
target_include_directories(test_filter_cache PRIVATE ${src_loc})
nice_target_sources(test_filter_cache ${src_loc}
PRIVATE
    tests/filter_cache_test.cpp
)
target_link_libraries(test_filter_cache PRIVATE
    desktop-app::lib_base
    desktop-app::external_qt
)
set_target_properties(test_filter_cache PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}
)
add_dependencies(Telegram test_filter_cache)

add_executable(test_filter_regex)
init_target(test_filter_regex "(tests)")
target_include_directories(test_filter_regex PRIVATE ${src_loc})
nice_target_sources(test_filter_regex ${src_loc}
PRIVATE
    tests/filter_regex_test.cpp
)
target_link_libraries(test_filter_regex PRIVATE ayugram::lib_icu)
set_target_properties(test_filter_regex PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}
)
add_dependencies(Telegram test_filter_regex)


if (APPLE AND CMAKE_OSX_ARCHITECTURES MATCHES "arm64")
    add_executable(test_media_streaming_benchmark)
    init_target(test_media_streaming_benchmark "(tests)")
    add_dependencies(test_media_streaming_benchmark td_scheme_scheme)
    set(streaming_benchmark_source_loc ${src_loc})
    if (DEFINED TDESKTOP_STREAMING_BENCHMARK_SOURCE_ROOT)
        set(streaming_benchmark_source_loc
            ${TDESKTOP_STREAMING_BENCHMARK_SOURCE_ROOT}
        )
        target_include_directories(test_media_streaming_benchmark BEFORE
            PRIVATE ${streaming_benchmark_source_loc}
        )
    endif()
    target_precompile_headers(test_media_streaming_benchmark PRIVATE
        $<$<COMPILE_LANGUAGE:CXX,OBJCXX>:${src_loc}/tests/streaming_benchmark_pch.h>
    )
    target_include_directories(test_media_streaming_benchmark PRIVATE
        ${src_loc}
        ${CMAKE_SOURCE_DIR}/Telegram/lib_tl
        ${CMAKE_BINARY_DIR}/Telegram/gen
    )
    target_compile_options(test_media_streaming_benchmark PRIVATE
        "-I${CMAKE_SOURCE_DIR}/Telegram/lib_tl"
        "-I${libs_loc}/local/include/AL"
    )
    nice_target_sources(test_media_streaming_benchmark ${src_loc}
    PRIVATE
        tests/streaming_benchmark.cpp
        tests/streaming_benchmark_stubs.cpp
        data/data_audio_msg_id.cpp
    )
    nice_target_sources(test_media_streaming_benchmark
        ${streaming_benchmark_source_loc}
    PRIVATE
        media/streaming/media_streaming_file.cpp
        media/streaming/media_streaming_loader.cpp
        media/streaming/media_streaming_player.cpp
        media/streaming/media_streaming_reader.cpp
        media/streaming/media_streaming_video_track.cpp
        media/streaming/media_streaming_audio_track.cpp
        media/streaming/media_streaming_utility.cpp
    )
    nice_target_sources(test_media_streaming_benchmark ${src_loc}
    PRIVATE
        media/streaming/media_streaming_native_frame_mac.mm
    )
    target_compile_definitions(test_media_streaming_benchmark PRIVATE
        "TDESKTOP_STREAMING_BENCHMARK_FIXTURE=\"${CMAKE_SOURCE_DIR}/Telegram/SourceFiles/test/data/media_streaming/streaming_720p.mp4\""
    )
    target_link_libraries(test_media_streaming_benchmark PRIVATE
        desktop-app::lib_base
        desktop-app::lib_crl
        desktop-app::lib_ui
        desktop-app::lib_storage
        desktop-app::lib_ffmpeg
        ${libs_loc}/local/lib/libdav1d.a
        desktop-app::external_openal
        desktop-app::external_qt
    )
    set_target_properties(test_media_streaming_benchmark PROPERTIES
        RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}
    )
endif()
