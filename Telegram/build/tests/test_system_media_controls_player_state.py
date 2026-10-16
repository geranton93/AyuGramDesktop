"""Keep system media controls tied to live players and release session media."""
import os
import pathlib
import unittest

SOURCE = pathlib.Path(os.environ.get(
    "AYU_SOURCE_ROOT",
    pathlib.Path(__file__).resolve().parents[2] / "SourceFiles",
))


def function_definition(source, signature):
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 1
    cursor = opening + 1
    while depth:
        if source[cursor] == "{":
            depth += 1
        elif source[cursor] == "}":
            depth -= 1
        cursor += 1
    return source[start:cursor]


class SystemMediaControlsPlayerState(unittest.TestCase):
    def test_state_selects_only_a_current_non_stopped_player(self):
        path = SOURCE / "media/system_media_controls_manager.cpp"
        source = path.read_text()
        helper = function_definition(source, "ActivePlayerType()")
        self.assertIn("player->current(type)", helper)
        self.assertIn("!IsStoppedOrStopping(player->getState(type).state)", helper)

        sync = function_definition(
            source,
            "void SystemMediaControlsManager::syncPlayerStateToControls()",
        )
        self.assertIn("const auto type = ActivePlayerType()", sync)
        self.assertIn("if (!type)", sync)
        self.assertNotIn("current(AudioMsgId::Type::Song)", sync)

    def test_final_audio_stop_releases_session_owned_media_objects(self):
        path = SOURCE / "media/system_media_controls_manager.cpp"
        source = path.read_text()
        constructor = function_definition(
            source,
            "SystemMediaControlsManager::SystemMediaControlsManager()",
        )
        stop_merge = constructor.index(
            "rpl::merge(\n\t\tmediaPlayer->stops(AudioMsgId::Type::Song),"
        )
        cleanup_end = constructor.index("auto trackChanged", stop_merge)
        cleanup = constructor[stop_merge:cleanup_end]
        self.assertIn("if (!ActivePlayerType())", cleanup)
        inactive_guard = cleanup.index("if (!ActivePlayerType())")
        release_cached = cleanup.index("_cachedMediaView = nullptr;")
        release_stream = cleanup.index("_streamed = nullptr;", release_cached)
        self.assertLess(inactive_guard, release_cached)
        self.assertLess(release_cached, release_stream)

    def test_no_active_audio_cancels_pending_media_and_stream_state(self):
        path = SOURCE / "media/system_media_controls_manager.cpp"
        source = path.read_text()
        sync = function_definition(
            source,
            "void SystemMediaControlsManager::syncPlayerStateToControls()",
        )
        no_active = function_definition(sync, "if (!type)")
        operations = (
            "_lifetimeDownload.destroy();",
            "_lastAudioMsgId = AudioMsgId();",
            "_cachedMediaView = nullptr;",
            "_streamed = nullptr;",
            "_controls->setEnabled(false);",
            "_controls->clearMetadata();",
            "return;",
        )
        positions = [no_active.index(operation) for operation in operations]

        self.assertEqual(positions, sorted(positions))

    def test_song_and_voice_events_reselect_the_live_player(self):
        path = SOURCE / "media/system_media_controls_manager.cpp"
        source = path.read_text()
        constructor = function_definition(
            source,
            "SystemMediaControlsManager::SystemMediaControlsManager()",
        )
        activity_start = constructor.index(
            "mediaPlayer->stops(AudioMsgId::Type::Song) | rpl::to_empty"
        )
        activity_end = constructor.index("auto trackChanged", activity_start)
        activity = constructor[activity_start:activity_end]
        for event in (
            "mediaPlayer->stops(AudioMsgId::Type::Song)",
            "mediaPlayer->startsPlay(AudioMsgId::Type::Song)",
            "mediaPlayer->stops(AudioMsgId::Type::Voice)",
            "mediaPlayer->startsPlay(AudioMsgId::Type::Voice)",
        ):
            self.assertIn(event, activity)
        self.assertIn("syncPlayerStateToControls();", activity)
        self.assertNotIn("rpl::map_to(false)", activity)
        self.assertNotIn("rpl::distinct_until_changed()", activity)

    def test_status_and_playlist_controls_use_the_selected_active_type(self):
        path = SOURCE / "media/system_media_controls_manager.cpp"
        source = path.read_text()
        constructor = function_definition(
            source,
            "SystemMediaControlsManager::SystemMediaControlsManager()",
        )
        notifier_start = constructor.index("mediaPlayer->updatedNotifier(")
        activity_start = constructor.index(
            "mediaPlayer->stops(AudioMsgId::Type::Song) | rpl::to_empty"
        )
        notifier = constructor[notifier_start:activity_start]
        self.assertIn("const auto type = ActivePlayerType();", notifier)
        self.assertIn("mediaPlayer->getState(*type)", notifier)

        playlist_start = constructor.index(
            "mediaPlayer->playlistChanges(AudioMsgId::Type::Song)"
        )
        playlist_end = constructor.index("using Media::RepeatMode;", playlist_start)
        playlist = constructor[playlist_start:playlist_end]
        self.assertIn("if (const auto type = ActivePlayerType())", playlist)
        self.assertIn("mediaPlayer->nextAvailable(*type)", playlist)

    def test_same_track_resync_keeps_pending_thumbnail_download(self):
        path = SOURCE / "media/system_media_controls_manager.cpp"
        source = path.read_text()
        apply_track = function_definition(
            source,
            "void SystemMediaControlsManager::applyPlayerTrack(",
        )
        same_track_guard = apply_track.index(
            "if ((_lastAudioMsgId.contextId() == current.contextId())"
        )
        same_track_return = apply_track.index("return;", same_track_guard)
        destroy_subscription = apply_track.index("_lifetimeDownload.destroy();")
        replace_track = apply_track.index("const auto document = current.audio();")

        self.assertLess(same_track_guard, same_track_return)
        self.assertLess(same_track_return, destroy_subscription)
        self.assertLess(destroy_subscription, replace_track)

    def test_track_change_releases_the_previous_cover_view(self):
        path = SOURCE / "media/system_media_controls_manager.cpp"
        source = path.read_text()
        apply_track = function_definition(
            source,
            "void SystemMediaControlsManager::applyPlayerTrack(",
        )
        same_track_return = apply_track.index(
            "return;",
            apply_track.index(
                "if ((_lastAudioMsgId.contextId() == current.contextId())"
            ),
        )
        clear_cached_view = apply_track.index("_cachedMediaView = nullptr;")
        select_voice_path = apply_track.index(
            "if (audioType == AudioMsgId::Type::Voice)"
        )

        self.assertLess(same_track_return, clear_cached_view)
        self.assertLess(clear_cached_view, select_voice_path)

    def test_video_finish_invalidates_audio_id_before_sync(self):
        path = SOURCE / "media/system_media_controls_manager.cpp"
        source = path.read_text()
        finish = function_definition(
            source,
            "void SystemMediaControlsManager::videoFinish(",
        )
        release_video_controls = finish.index("_videoDelegate = nullptr;")
        invalidate_audio_track = finish.index("_lastAudioMsgId = AudioMsgId();")
        restore_audio_controls = finish.index("syncPlayerStateToControls();")

        self.assertLess(release_video_controls, invalidate_audio_track)
        self.assertLess(invalidate_audio_track, restore_audio_controls)


if __name__ == "__main__":
    unittest.main()
