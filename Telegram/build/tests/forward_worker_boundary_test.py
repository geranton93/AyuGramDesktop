"""Source-contract regressions for the forwarding worker boundary.

These deliberately check threading/lifetime contracts, not GUI runtime behavior.
"""
from pathlib import Path
import os
import re
import shlex
import subprocess
import tempfile
import unittest
from contextlib import nullcontext

ROOT = Path(__file__).resolve().parents[3] / "Telegram" / "SourceFiles"
FORWARD = ROOT / "ayu/features/forward"


def test_temporary_directory(prefix):
    root = os.environ.get("AYU_TEST_PERSIST_TEMP_ROOT")
    if root:
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        return nullcontext(tempfile.mkdtemp(prefix=prefix, dir=root))
    return tempfile.TemporaryDirectory(prefix=prefix)


def function_body(text, name):
    start = text.index("void " + name + "(")
    opening = text.index("{", start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


def named_function_body(text, signature):
    start = text.index(signature)
    opening = text.index("{", start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


def braced_body_after(text, start):
    opening = text.index("{", start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[opening + 1:end - 1], end


def call_bodies(text, signature):
    bodies = []
    position = 0
    while True:
        start = text.find(signature, position)
        if start < 0:
            return bodies
        body, position = braced_body_after(text, start)
        bodies.append(body)


class ForwardWorkerBoundaryTest(unittest.TestCase):
    def test_resolved_continuation_runs_on_worker_with_weak_session(self):
        body = function_body((FORWARD / "ayu_forward.cpp").read_text(), "intelligentForward")
        continuation = body[body.index("if (resolvedIds.empty())"):]
        self.assertIn("crl::async(", continuation)
        self.assertNotIn("weakSession.get()", continuation)
        self.assertRegex(continuation, r"ContinueIntelligentForward\(\s*weakSession,")

    def test_context_menu_passes_existing_weak_handles(self):
        source = (ROOT / "ayu/ui/context_menu/context_menu.cpp").read_text()
        for name in ("forwardRichMessage", "forwardMessages", "intelligentForward"):
            calls = re.findall(r"AyuForward::" + name + r"\(\s*([^,]+),", source)
            self.assertTrue(calls, name)
            self.assertTrue(all(arg.strip() == "sourceSession" for arg in calls), name)

    def test_successful_intermediate_chunks_keep_forward_open(self):
        body = named_function_body(
            (FORWARD / "ayu_forward.cpp").read_text(),
            "Api::ForwardMessagesResult ContinueIntelligentForward(")
        successful_chunk = body[body.index("hasSucceeded = true;"):]
        self.assertNotIn("ForwardState::State::Finished", successful_chunk)
        self.assertIn(
            "state->updateBottomBar(&peerId, ForwardState::State::Sending);",
            successful_chunk)
        self.assertIn("FinishForward(state, peerId);\n\treturn hasUntrackedSends", body)

    def test_album_progress_counts_all_items_after_successful_send(self):
        body = named_function_body(
            (FORWARD / "ayu_forward.cpp").read_text(),
            "Api::ForwardMessagesResult forwardMessages(")
        self.assertIn("auto itemProgressCount = 1;", body)
        self.assertIn("progressSentMessages += itemProgressCount;", body)
        self.assertIn("itemProgressCount = groupItemCount;", body)
        self.assertNotIn("setSentMessages(++progressSentMessages)", body)

    def test_prepared_album_items_do_not_keep_the_album_alive(self):
        local_header = (ROOT / "storage/localimageloader.h").read_text()
        local_source = (ROOT / "storage/localimageloader.cpp").read_text()
        sending = (ROOT / "api/api_sending.cpp").read_text()

        descriptor = named_function_body(
            local_header, "struct FilePrepareDescriptor {")
        result = named_function_body(
            local_header, "struct FilePrepareResult {")
        constructor = named_function_body(
            local_source,
            "FilePrepareResult::FilePrepareResult(FilePrepareDescriptor &&descriptor)")

        self.assertIn("std::shared_ptr<SendingAlbum> album;", descriptor)
        self.assertIn("std::weak_ptr<SendingAlbum> album;", result)
        self.assertIn(", album(std::move(descriptor.album))", constructor)
        self.assertGreaterEqual(sending.count("file->album.lock()"), 2)
        self.assertIn("sample->album.lock()", sending)

    def test_late_send_cancellation_registration_stops_failed_batch(self):
        source = (ROOT / "api/api_sending.cpp").read_text()
        register = named_function_body(
            source, "void SendRequest::registerCancellation(")
        finish = named_function_body(source, "void SendRequest::finishOne(")
        self.assertIn("_state->cancelPending", register)
        self.assertIn("_state->cancelPending = true", finish)

    def test_failed_album_upload_cleans_published_placeholders(self):
        apiwrap = (ROOT / "apiwrap.cpp").read_text()
        send_files = named_function_body(apiwrap, "void ApiWrap::sendFiles(")
        group = send_files.index("const auto groupId = album->groupId;")
        registration = send_files.index(
            "sendRequest->registerCancellation([=]", group)
        cancellation = braced_body_after(send_files, registration)[0]

        compact = re.sub(r"\s+", "", cancellation)
        self.assertIn(
            "current->api()._sendingAlbums.take(groupId)", compact)
        self.assertIn(
            "current->data().unregisterMessageRandomId(part.randomId)",
            compact)
        self.assertIn("current->data().message(part.msgId)", compact)
        self.assertIn("pending && pending->isSending()", cancellation)
        self.assertIn(
            "current->data().destroyMessagesWithCacheCleanup(items)",
            compact)

        upload = named_function_body(
            apiwrap, "void ApiWrap::uploadAlbumMedia(")
        failed = braced_body_after(
            upload, upload.index("const auto failed ="))[0]
        self.assertIn(
            "sendRequest->finishOne(Api::SendResult::Failed)", failed)
        fail_handler = braced_body_after(
            upload, upload.index("}).fail([=]"))[0]
        self.assertIn("failed();", fail_handler)

        sending = (ROOT / "api/api_sending.cpp").read_text()
        finish = named_function_body(sending, "void SendRequest::finishOne(")
        self.assertRegex(
            finish,
            r"if \(--_state->remaining != 0\)\s*\{\s*return;")
        self.assertIn("_state->cancelPending = true", finish)
        self.assertLess(
            finish.index("cancellation();"),
            finish.index("callback(terminal)"))

    def test_cancelling_active_file_preparation_stops_worker_completion(self):
        queue = (ROOT / "storage/localimageloader.cpp").read_text()
        header = (ROOT / "storage/localimageloader.h").read_text()
        cancel = named_function_body(queue, "void TaskQueue::cancelTask(")
        worker = named_function_body(
            queue, "void TaskQueueWorker::onTaskAdded()")
        prepare = named_function_body(
            queue, "void FileLoadTask::process(ProcessArgs &&args)")
        self.assertIn("_taskInProcess->cancel()", cancel)
        self.assertRegex(
            worker,
            r"if \(!task->isCancelled\(\)\)\s*\{\s*"
            r"_queue->_tasksToFinish.push_back\(std::move\(task\)\);",
        )
        self.assertIn("std::shared_ptr<std::atomic<bool>> _cancelled", header)
        self.assertIn("_videoCover->shareCancellationWith(*this)", prepare)
        self.assertGreaterEqual(prepare.count("if (isCancelled())"), 3)

    def test_send_cancellation_is_bound_to_the_original_task_instance(self):
        header = (ROOT / "storage/localimageloader.h").read_text()
        queue = (ROOT / "storage/localimageloader.cpp").read_text()
        send_files = named_function_body(
            (ROOT / "apiwrap.cpp").read_text(), "void ApiWrap::sendFiles(")
        cancel = named_function_body(queue, "void TaskQueue::cancelTask(")
        self.assertIn("using TaskId = uint64", header)
        self.assertIn("const TaskId _id", header)
        self.assertIn("Task::Task()", queue)
        self.assertIn("NextTaskId.fetch_add", queue)
        self.assertIn("return task->id()", cancel)
        self.assertNotIn("cancellationToken", header)
        self.assertNotIn("cancellationToken", cancel)
        self.assertRegex(send_files, r"cancelTask\(\s*taskId\)")
        self.assertIn(
            "cancelTask(taskId)",
            named_function_body(
                (ROOT / "ayu/features/forward/ayu_sync.cpp").read_text(),
                "SendSyncResult sendVoiceSync("))
        voice_message = named_function_body(
            (ROOT / "apiwrap.cpp").read_text(),
            "void ApiWrap::sendVoiceMessage(")
        self.assertIn("cancelTask(taskId)", voice_message)

    def test_partial_success_in_multi_file_send_is_indeterminate(self):
        source = (ROOT / "api/api_sending.cpp").read_text()
        finish = named_function_body(source, "void SendRequest::finishOne(")
        self.assertIn("_state->remaining > 1", finish)
        self.assertIn("_state->succeeded > 0", finish)
        self.assertIn("++_state->succeeded", finish)
        self.assertIn("SendResult::Indeterminate", finish)

    def test_forward_cancellation_callbacks_run_on_main_thread(self):
        sync = (FORWARD / "ayu_sync.cpp").read_text()
        body = named_function_body(
            sync, "ForwardMessagesSyncResult forwardMessagesSync(")
        cancel_requests = named_function_body(
            body,
            "const auto cancelRequestsOnMain = [cancellations]",
        )
        self.assertEqual(cancel_requests.count("cancel();"), 1)
        self.assertNotIn("crl::on_main_sync(", cancel_requests)

        cancel_outstanding = braced_body_after(
            body,
            body.index("const auto cancelOutstanding ="),
        )[0]
        self.assertIn("crl::on_main_sync(cancelRequestsOnMain)", cancel_outstanding)
        self.assertNotIn("cancel();", cancel_outstanding)

        exception = braced_body_after(body, body.index("catch (...) {", body.index(
            "forwardMessagesWithResult(")))[0]
        self.assertIn("cancelRequestsOnMain();", exception)
        self.assertNotIn("crl::on_main_sync(", exception)
        self.assertNotIn("crl::async(", exception)

        apiwrap = named_function_body(
            (ROOT / "apiwrap.cpp").read_text(),
            "void ApiWrap::forwardMessagesInternal(",
        )
        api_cancellations = call_bodies(
            apiwrap,
            "shared->registerRequestCancellation(",
        )
        self.assertEqual(len(api_cancellations), 2)
        for cancellation in api_cancellations:
            self.assertNotIn("crl::on_main_sync(", cancellation)

    def test_rich_forward_waits_observe_cancellation_and_cancel_request(self):
        sync = (FORWARD / "ayu_sync.cpp").read_text()
        rich = (FORWARD / "ayu_forward_rich.cpp").read_text()
        for signature in (
                "std::shared_ptr<const Iv::RichPage> loadFullRichPageSync(",
                "SendSyncResult sendRichMessageSync("):
            with self.subTest(helper=signature):
                body = named_function_body(sync, signature)
                wait = body.index("WaitForDownload(")
                abandon = body.index("abandoned->store(true)", wait)
                cleanup = body.index(
                    "crl::on_main_sync([weakSession, requestId]",
                    abandon)
                self.assertLess(wait, abandon)
                self.assertLess(abandon, cleanup)
                self.assertRegex(body[wait:], r"WaitForDownload\([\s\S]*?cancelled\)")
                self.assertGreaterEqual(
                    body.count("(cancelled && cancelled())"),
                    2)
                self.assertIn(
                    "current->api().request(*requestId).cancel();",
                    body[cleanup:])
                self.assertNotIn("std::make_shared<MTP::Sender>", body)

        send = named_function_body(
            sync, "SendSyncResult sendRichMessageSync(")
        self.assertIn("return SendSyncResult::Indeterminate", send)
        self.assertIn("sendResult->store(SendSyncResult::Succeeded)", send)
        self.assertIn(
            "ToSendSyncResult(Api::SendResultForError(error))",
            send,
        )
        self.assertIn("return sendResult->load();", send)
        mapper = named_function_body(
            sync,
            "[[nodiscard]] SendSyncResult ToSendSyncResult("
            "Api::SendResult result)",
        )
        cpp = r'''#include <atomic>
namespace Api {
enum class SendResult { Succeeded, Failed, Indeterminate };
}
enum class SendSyncResult { Succeeded, Failed, Indeterminate };
''' + mapper + r'''
int main() {
    if (ToSendSyncResult(Api::SendResult::Succeeded)
        != SendSyncResult::Succeeded) {
        return 1;
    }
    if (ToSendSyncResult(Api::SendResult::Failed)
        != SendSyncResult::Failed) {
        return 2;
    }
    return ToSendSyncResult(Api::SendResult::Indeterminate)
        == SendSyncResult::Indeterminate ? 0 : 3;
}
'''
        with test_temporary_directory(
            prefix="ayu-send-result-map-",
        ) as directory:
            directory = Path(directory)
            source = directory / "test.cpp"
            binary = directory / "test"
            source.write_text(cpp)
            compiler = shlex.split(os.environ.get("CXX", "c++"))
            subprocess.run(compiler + [
                "-std=c++20", "-O0", "-g", "-Wall", "-Wextra", "-pedantic",
                str(source), "-o", str(binary),
            ], check=True, timeout=60)
            result = subprocess.run(
                [str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        forward = named_function_body(
            rich, "AyuSync::SendSyncResult forwardRichMessage(")
        self.assertRegex(
            forward,
            r"loadFullRichPageSync\(\s*session,\s*itemId,\s*cancelled\)")
        self.assertRegex(
            forward,
            r"sendRichMessageSync\([\s\S]*?std::move\(targetHistory\),\s*cancelled\)")

    def test_upload_media_rpc_uses_the_session_owned_sender(self):
        sync = (FORWARD / "ayu_sync.cpp").read_text()
        upload = named_function_body(sync, "UploadedFile uploadFileSync(")
        self.assertIn("current->api().request(MTPmessages_UploadMedia", upload)
        self.assertIn("current->api().request(*mediaRequestId).cancel()", upload)
        self.assertNotIn("std::make_shared<MTP::Sender>", upload)

    def test_rich_forward_wait_returns_on_cancellation(self):
        wait_header = (FORWARD / "ayu_sync_wait.h").read_text()
        wait_function = named_function_body(
            wait_header,
            "DownloadWaitResult WaitForDownload(")
        cpp = r'''
#include <algorithm>
#include <chrono>
#include <functional>
#include <thread>
template <typename Signature>
using Fn = std::function<Signature>;
enum class DownloadWaitResult { Completed, Cancelled, TimedOut };
struct TimedCountDownLatch {
    bool ready = false;
    bool await(std::chrono::milliseconds timeout) {
        if (timeout > std::chrono::milliseconds(0)) {
            std::this_thread::sleep_for(timeout);
        }
        return ready;
    }
};
''' + wait_function + r'''
int main() {
    auto done = TimedCountDownLatch();
    const auto cancelled = [] { return true; };
    if (WaitForDownload(done, std::chrono::minutes(1), cancelled)
        != DownloadWaitResult::Cancelled) {
        return 1;
    }
    done.ready = true;
    if (WaitForDownload(done, std::chrono::minutes(1), cancelled)
        != DownloadWaitResult::Completed) {
        return 2;
    }
    return 0;
}
'''
        with test_temporary_directory(
            prefix="ayu-forward-cancel-wait-",
        ) as directory:
            directory = Path(directory)
            source = directory / "test.cpp"
            binary = directory / "test"
            source.write_text(cpp)
            compiler = shlex.split(os.environ.get("CXX", "c++"))
            subprocess.run(compiler + [
                "-std=c++20", "-O0", "-g", "-Wall", "-Wextra", "-pedantic",
                str(source), "-o", str(binary),
            ], check=True, timeout=60)
            result = subprocess.run(
                [str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_regular_media_forward_waits_for_correlated_send_result(self):
        sync = (FORWARD / "ayu_sync.cpp").read_text()
        document = named_function_body(sync, "SendSyncResult sendDocumentSync(")
        voice = named_function_body(sync, "SendSyncResult sendVoiceSync(")
        self.assertNotIn("waitForMsgSync", sync)
        self.assertIn("waitForSendRequest(", document)
        self.assertIn("std::move(request)", document)
        self.assertIn("waitForSendRequest(", voice)
        self.assertIn("request->registerCancellation", voice)
        self.assertRegex(
            voice,
            r"cancelTask\(\s*taskId\)")
        forward = (FORWARD / "ayu_forward.cpp").read_text()
        media = named_function_body(
            forward, "AyuSync::SendSyncResult sendMedia(")
        continuation = named_function_body(
            forward, "Api::ForwardMessagesResult forwardMessages(")
        self.assertIn("return AyuSync::SendSyncResult::Succeeded", media)
        self.assertNotIn("return false", media)
        self.assertIn("itemSendResult = sendMedia(", continuation)
        self.assertIn("itemProgressKnown = itemSucceeded", continuation)
        self.assertNotIn("hasUntrackedSends", continuation)

    def test_forward_rpc_failure_clears_random_ids_after_placeholder_removal(self):
        source = (ROOT / "apiwrap.cpp").read_text()
        body = named_function_body(
            source, "void ApiWrap::forwardMessagesInternal(")
        failure_start = body.index("const auto fail =")
        failure_end = body.index("const auto sendPrepared =", failure_start)
        failure = body[failure_start:failure_end]
        self.assertRegex(
            failure,
            r"sendMessageFail\(\s*error,\s*peer,\s*randomId,\s*itemId\);"
            r"\s*_session->data\(\).unregisterMessageRandomId\(randomId\);",
        )

    def test_file_upload_and_album_preserve_send_result(self):
        apiwrap = (ROOT / "apiwrap.cpp").read_text()
        send_files = named_function_body(apiwrap, "void ApiWrap::sendFiles(")
        uploaded_document = named_function_body(
            apiwrap, "void ApiWrap::sendUploadedDocument(")
        upload_album = named_function_body(apiwrap, "void ApiWrap::uploadAlbumMedia(")
        send_media = named_function_body(apiwrap, "void ApiWrap::sendMediaWithRandomId(")
        single_media_failure_start = send_media.index(
            "}, [=](const MTP::Error &error")
        single_media_failure = send_media[single_media_failure_start:]
        send_album = named_function_body(apiwrap, "void ApiWrap::sendAlbumIfReady(")
        ephemeral_send_start = send_media.index(
            "if (_session->ephemeralMessages().sendMedia")
        ephemeral_send_end = send_media.index(
            "auto caption = item->originalText();", ephemeral_send_start)
        ephemeral_send = send_media[ephemeral_send_start:ephemeral_send_end]
        ephemeral_album_start = send_album.index(
            "if (const auto target = _session->data().message(replyTo.messageId)")
        ephemeral_album_end = send_album.index(
            "if (album->options.price > 0)", ephemeral_album_start)
        ephemeral_album = send_album[ephemeral_album_start:ephemeral_album_end]
        existing_media = named_function_body(
            (ROOT / "api/api_sending.cpp").read_text(),
            "void SendExistingMedia(")
        existing_ephemeral_start = existing_media.index(
            "if (session->ephemeralMessages().sendMedia(")
        existing_ephemeral_end = existing_media.index(
            "const auto performRequest =", existing_ephemeral_start)
        existing_ephemeral = existing_media[
            existing_ephemeral_start:existing_ephemeral_end]
        upload_failures = named_function_body(
            (ROOT / "storage/file_upload.cpp").read_text(),
            "void Uploader::notifyFailed(")
        self.assertIn(".sendRequest = sendRequest", send_files)
        self.assertRegex(
            send_files,
            r"cancelTask\(\s*taskId\)")
        self.assertIn("std::move(sendRequest)", uploaded_document)
        self.assertIn("MTPmessages_UploadMedia", upload_album)
        self.assertIn("registerCancellation", upload_album)
        self.assertIn("finishOne(Api::SendResult::Succeeded)", send_media)
        self.assertIn(
            "finishOne(Api::SendResultForError(error))",
            send_media,
        )
        self.assertIn("finishOne(Api::SendResult::Indeterminate)", ephemeral_send)
        self.assertNotIn("SendResult::Succeeded", ephemeral_send)
        self.assertIn("unregisterMessageRandomId(randomId)", ephemeral_send)
        self.assertIn("unregisterMessageRandomId(randomId)", existing_ephemeral)
        welcome_start = send_media.index("if (options.welcomeTemplate)")
        welcome_end = send_media.index("applyGhostScheduling", welcome_start)
        self.assertIn(
            "unregisterMessageRandomId(randomId)",
            send_media[welcome_start:welcome_end])
        self.assertIn(
            "unregisterMessageRandomId(randomId)",
            send_media[:welcome_start])
        self.assertIn("sendRequest->finishOne(result)", ephemeral_album)
        self.assertIn("result = Api::SendResult::Indeterminate", ephemeral_album)
        self.assertIn("unregisterMessageRandomId(part.randomId)", ephemeral_album)
        self.assertIn("sendMultiPaidMedia(sample, album, nullptr, sendRequest)", send_album)
        self.assertIn("finishOne(Api::SendResult::Succeeded)", send_album)
        self.assertLess(
            single_media_failure.index("sendMessageFail(error, peer, randomId, itemId)"),
            single_media_failure.index("unregisterMessageRandomId(randomId)"))
        self.assertIn("_videoWaitingCover.take(entry.itemId)", upload_failures)
        self.assertIn("video->sendRequest->finishOne(Api::SendResult::Failed)", upload_failures)
        uploader = (ROOT / "storage/file_upload.cpp").read_text()
        cover_upload = named_function_body(
            uploader, "void Uploader::uploadCoverAsPhoto(")
        cancel_upload = named_function_body(
            uploader, "void Uploader::failed(")
        self.assertIn("_videoCoverUploadRequests.emplace(videoId, requestId)",
                      cover_upload)
        self.assertIn("_videoCoverUploadRequests.take(itemId)", cancel_upload)
        self.assertIn("_api->request(*requestId).cancel()", cancel_upload)

    def test_uploader_clear_completes_pending_sends_as_indeterminate(self):
        source = (ROOT / "storage/file_upload.cpp").read_text()
        clear = named_function_body(source, "void Uploader::clear()")

        self.assertIn("sendRequests.push_back(entry.file->sendRequest);", clear)
        self.assertIn("for (const auto &entry : _videoWaitingCover)", clear)
        self.assertIn("sendRequests.push_back(video.sendRequest);", clear)
        self.assertIn("_videoWaitingCover.clear();", clear)
        self.assertIn("_videoIdToCoverId.clear();", clear)
        self.assertIn("_videoCoverUploadRequests", clear)
        self.assertIn("_api->request(request.second).cancel();", clear)
        self.assertIn("request->finishOne(Api::SendResult::Indeterminate);", clear)

        cover_requests = clear.index("base::take(_videoCoverUploadRequests)")
        queue_clear = clear.index("_queue.clear();")
        cover_clear = clear.index("_videoWaitingCover.clear();")
        cover_cancel = clear.index("_api->request(request.second).cancel();")
        stop_sessions = clear.index("stopSessions();")
        terminalize = clear.index(
            "request->finishOne(Api::SendResult::Indeterminate);")
        self.assertLess(cover_requests, cover_clear)
        self.assertLess(queue_clear, terminalize)
        self.assertLess(cover_clear, terminalize)
        self.assertLess(cover_clear, cover_cancel)
        self.assertLess(cover_cancel, terminalize)
        self.assertLess(stop_sessions, terminalize)

    def test_single_item_album_preflight_runs_registered_cancel_cleanup(self):
        source = (ROOT / "apiwrap.cpp").read_text()
        send_media = named_function_body(
            source, "void ApiWrap::sendMediaWithRandomId(")
        preflight = braced_body_after(
            send_media,
            send_media.index("if (sendRequest && sendRequest->shouldCancel())"),
        )[0]
        self.assertIn("unregisterMessageRandomId(randomId)", preflight)
        self.assertIn("sendRequest->cancel()", preflight)

        send_album = named_function_body(
            source, "void ApiWrap::sendAlbumIfReady(")
        single_item = braced_body_after(
            send_album,
            send_album.index("} else if (medias.size() < 2) {"),
        )[0]
        self.assertLess(
            single_item.index("sendMediaWithRandomId("),
            single_item.index("_sendingAlbums.remove(groupId);"))

        failure_start = send_album.index(
            "}, [=](const MTP::Error &error, const MTP::Response &response)")
        failure = braced_body_after(send_album, failure_start)[0]
        self.assertIn("sendRequest->shouldCancel()", failure)
        self.assertLess(
            failure.index("sendRequest->cancel();"),
            failure.index("sendMessageFail("))

    def test_album_cancellation_and_paid_send_release_part_random_ids(self):
        source = (ROOT / "apiwrap.cpp").read_text()
        send_files = named_function_body(source, "void ApiWrap::sendFiles(")
        cancel_album = named_function_body(
            source, "void ApiWrap::sendAlbumWithCancelled(")
        paid = named_function_body(
            source, "void ApiWrap::sendMultiPaidMedia(")
        self.assertIn("_sendingAlbums.take(", send_files)
        self.assertIn("part.randomId", send_files)
        self.assertIn("unregisterMessageRandomId(", send_files)
        self.assertIn("if (!album->sent)", cancel_album)
        self.assertIn("&SendingAlbum::Item::msgId", cancel_album)
        self.assertIn("unregisterMessageRandomId(pending->randomId)", cancel_album)
        self.assertLess(
            cancel_album.index("unregisterMessageRandomId(pending->randomId)"),
            cancel_album.index("album->removeItem(item)"))
        self.assertIn("partRandomIds", paid)
        self.assertIn("unregisterMessageRandomId(partRandomId)", paid)
        paid_failure_start = paid.index(
            "}, [=](const MTP::Error &error, const MTP::Response &response)")
        paid_failure, _ = braced_body_after(paid, paid_failure_start)
        self.assertIn("_sendingAlbums.take(groupId)", paid_failure)
        part_failure_start = paid_failure.index(
            "for (const auto &part : (*album)->items)")
        part_failure, part_failure_end = braced_body_after(
            paid_failure,
            part_failure_start)
        self.assertIn(
            "sendMessageFail(error, peer, part.randomId, part.msgId);",
            part_failure)
        self.assertLess(
            paid_failure.index("_sendingAlbums.take(groupId)"),
            part_failure_start)
        self.assertLess(
            part_failure_end,
            paid_failure.index("unregisterMessageRandomId(partRandomId)"))
        self.assertLess(
            paid_failure.index("unregisterMessageRandomId(partRandomId)"),
            paid_failure.index(
                "sendRequest->finishOne(Api::SendResultForError(error))"))
        multi_album = named_function_body(
            source, "void ApiWrap::sendAlbumIfReady(")
        self.assertIn("unregisterMessageRandomId(randomId)", multi_album)
        failure_start = multi_album.index("}, [=](const MTP::Error &error")
        failure = multi_album[failure_start:]
        self.assertIn("for (const auto randomId : randomIds)", failure)
        self.assertLess(
            failure.index("sendMessageFail(error, peer);"),
            failure.index("unregisterMessageRandomId(randomId)"))
        self.assertLess(
            failure.index("unregisterMessageRandomId(randomId)"),
            failure.index(
                "sendRequest->finishOne(Api::SendResultForError(error))"))


    def test_rich_forward_preserves_api_result_classification(self):
        rich = (FORWARD / "ayu_forward_rich.cpp").read_text()
        self.assertIn(
            "AyuSync::SendSyncResult forwardRichMessage(",
            rich)
        self.assertIn("return AyuSync::sendRichMessageSync(", rich)

        forward = (FORWARD / "ayu_forward.cpp").read_text()
        body = named_function_body(
            forward, "Api::ForwardMessagesResult forwardMessages(")
        start = body.index("if (item.richPage)")
        end = body.index("const auto &extractedText = item.text;", start)
        rich_branch = body[start:end]
        self.assertIn("itemSendResult = forwardRichMessage(", rich_branch)
        self.assertIn("itemSucceeded = itemSendResult", rich_branch)
        self.assertRegex(
            rich_branch,
            r"return\s*\(\s*itemSendResult\s*==\s*"
            r"AyuSync::SendSyncResult::Failed\s*&&\s*!didSendAnything\s*"
            r"\)\s*\?\s*Api::ForwardMessagesResult::Failed\s*"
            r":\s*Api::ForwardMessagesResult::Indeterminate;",
        )
        failure_guard = rich_branch.index("if (!itemSucceeded)")
        success_record = rich_branch.index("didSendAnything = true;")
        self.assertLess(failure_guard, success_record)
        self.assertRegex(rich_branch, r"didSendAnything = true;\s*continue;")
        self.assertNotIn("hasSkippedItems = true", rich_branch)


if __name__ == "__main__":
    unittest.main()
