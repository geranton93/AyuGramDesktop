"""Check shared document-download ownership and waveform byte access."""
import os
import pathlib
import shlex
import subprocess
import tempfile
import unittest
from contextlib import nullcontext

SOURCE = pathlib.Path(__file__).resolve().parents[2] / "SourceFiles"


def test_temporary_directory(prefix):
    root = os.environ.get("AYU_TEST_PERSIST_TEMP_ROOT")
    if root:
        root = pathlib.Path(root)
        root.mkdir(parents=True, exist_ok=True)
        return nullcontext(tempfile.mkdtemp(prefix=prefix, dir=root))
    return tempfile.TemporaryDirectory(prefix=prefix)


def block_body(source, signature):
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
    return source[opening + 1:cursor - 1]


def compile_and_run(cpp):
    with test_temporary_directory(prefix="ayu-shared-download-") as directory:
        directory = pathlib.Path(directory)
        test_source = directory / "test.cpp"
        binary = directory / "test"
        test_source.write_text(cpp)
        compiler = shlex.split(os.environ.get("CXX", "c++"))
        subprocess.run(compiler + [
            "-std=c++20", "-O0", "-g", "-Wall", "-Wextra", "-pedantic",
            str(test_source), "-o", str(binary),
        ], check=True, timeout=60)
        result = subprocess.run(
            [str(binary)], capture_output=True, text=True, timeout=10)
        if result.returncode:
            raise AssertionError(
                f"exit {result.returncode}\n{result.stdout}{result.stderr}")


class AyuSyncSharedDownload(unittest.TestCase):
    def test_user_cancel_sends_one_terminal_document_update(self):
        source = (SOURCE / "data/data_document.cpp").read_text()
        cancel = block_body(source, "void DocumentData::cancel()")
        destroy_loader = block_body(
            source,
            "void DocumentData::destroyLoader()",
        )
        cpp = r'''#include <memory>
#include <utility>

struct DocumentData;

struct Owner {
    void documentLoadDone(DocumentData*) { ++doneCalls; }
    int doneCalls = 0;
    int loaderCancelCalls = 0;
};

struct Loader {
    struct Lifetime {
        void destroy() { active = false; }
        bool active = true;
    };

    explicit Loader(DocumentData *owner) : _owner(owner) {}
    Lifetime &lifetime() { return _lifetime; }
    bool cancelled() const { return _cancelled; }
    void cancel();

    DocumentData *_owner = nullptr;
    Lifetime _lifetime;
    bool _cancelled = false;
};

namespace base {
template <typename Type>
auto take(Type &value) {
    return std::exchange(value, Type());
}
}

struct DocumentData {
    enum class Flag : int { DownloadCancelled = 1 };
    struct Flags {
        Flags &operator|=(Flag flag) {
            value |= int(flag);
            return *this;
        }
        int value = 0;
    };

    explicit DocumentData(Owner *owner)
    : _owner(owner)
    , _loader(std::make_unique<Loader>(this)) {}

    bool loading() const { return bool(_loader); }
    bool cancelled() const {
        return (_flags.value & int(Flag::DownloadCancelled)) != 0;
    }
    void loaderDone() { _owner->documentLoadDone(this); }
    void destroyLoader();
    void cancel();

    Flags _flags;
    Owner *_owner = nullptr;
    std::unique_ptr<Loader> _loader;
};

void Loader::cancel() {
    _cancelled = true;
    ++_owner->_owner->loaderCancelCalls;
    if (_lifetime.active) {
        _owner->loaderDone();
    }
}

void DocumentData::destroyLoader() {''' + destroy_loader + r'''}

void DocumentData::cancel() {''' + cancel + r'''}

int main() {
    auto owner = Owner();
    auto document = DocumentData(&owner);
    document.cancel();
    return (owner.doneCalls == 1)
        && (owner.loaderCancelCalls == 1)
        && !document.loading()
        && document.cancelled()
        ? 0
        : 1;
}
'''
        compile_and_run(cpp)

    def test_response_parse_failure_is_not_a_confirmed_send_failure(self):
        api_sending = (SOURCE / "api/api_sending.cpp").read_text()
        api_sending_header = (SOURCE / "api/api_sending.h").read_text()
        apiwrap = (SOURCE / "apiwrap.cpp").read_text()
        self.assertIn("SendResultForError(", api_sending_header)
        self.assertIn(
            'error.type() == u"CLIENT_RESPONSE_PARSE_FAILED"_q',
            block_body(api_sending, "SendResult SendResultForError("),
        )
        self.assertEqual(
            api_sending.count("finishOne(SendResultForError(error))"),
            2,
        )
        self.assertEqual(
            apiwrap.count("finishOne(Api::SendResultForError(error))"),
            3,
        )
        self.assertIn(
            "completion->requestFinished(Api::SendResultForError(error));",
            apiwrap,
        )
        self.assertIn(
            "shared->requestFinished(\n"
            "\t\t\t\t\t\tApi::SendResultForError(error),",
            apiwrap,
        )
        helper = block_body(api_sending, "SendResult SendResultForError(")
        cpp = r'''#include <cstddef>
#include <string>
std::u16string operator""_q(const char16_t *value, std::size_t size) {
    return std::u16string(value, size);
}
namespace MTP {
struct Error {
    const std::u16string &type() const { return value; }
    std::u16string value;
};
}
namespace Api {
enum class SendResult { Failed, Indeterminate };
SendResult SendResultForError(const MTP::Error &error) {''' + helper + r'''}
}
int main() {
    auto parseFailure = MTP::Error{u"CLIENT_RESPONSE_PARSE_FAILED"};
    if (Api::SendResultForError(parseFailure)
        != Api::SendResult::Indeterminate) {
        return 1;
    }
    auto rpcFailure = MTP::Error{u"FLOOD_WAIT_5"};
    return Api::SendResultForError(rpcFailure) == Api::SendResult::Failed
        ? 0
        : 2;
}
'''
        compile_and_run(cpp)

    def test_only_current_exclusive_shared_loader_can_be_cancelled(self):
        source = (SOURCE / "data/data_document.cpp").read_text()
        decision = block_body(
            source,
            "bool CanCancelSharedDocumentLoad(",
        )
        cpp = r'''#include <algorithm>
#include <cstddef>
#include <cstdint>
using uint64 = std::uint64_t;
bool CanCancelSharedDocumentLoad(
        uint64 requestedGeneration,
        uint64 activeGeneration,
        bool hasOrdinarySaveDemand) {''' + decision + r'''}
int main() {
    if (!CanCancelSharedDocumentLoad(11, 11, false)) return 1;
    if (CanCancelSharedDocumentLoad(11, 11, true)) return 2;
    if (CanCancelSharedDocumentLoad(10, 11, false)) return 3;
    if (CanCancelSharedDocumentLoad(0, 0, false)) return 4;
    return 0;
}
'''
        compile_and_run(cpp)

    def test_auxiliary_cancellation_does_not_leave_user_cancelled_state(self):
        source = (SOURCE / "data/data_document.cpp").read_text()
        decision = block_body(
            source,
            "bool CanCancelSharedDocumentLoad(",
        )
        cancel = block_body(
            source,
            "bool DocumentData::cancelSharedDownloadIfUnused(",
        )
        cpp = r'''#include <cstdint>
#include <cstdlib>
#include <memory>
#include <utility>
using uint64 = std::uint64_t;
bool CanCancelSharedDocumentLoad(
        uint64 requestedGeneration,
        uint64 activeGeneration,
        bool hasOrdinarySaveDemand) {''' + decision + r'''}
namespace base {
template <typename Type>
auto take(Type &value) {
    return std::exchange(value, Type());
}
}
struct DocumentData;
struct Owner {
    void documentLoadDone(DocumentData *) {
        ++doneCalls;
    }
    int doneCalls = 0;
};
struct LoaderStats {
    bool cancelled = false;
    bool callbackActive = true;
    bool *documentCancelled = nullptr;
};
struct Loader {
    struct Lifetime {
        void destroy() {
            stats->callbackActive = false;
        }
        LoaderStats *stats = nullptr;
    };
    Loader(LoaderStats *stats, bool *documentCancelled)
    : _stats(stats)
    , _documentCancelled(documentCancelled) {
        _lifetime.stats = stats;
    }
    ~Loader() {
        if (!finished) std::abort();
    }
    Lifetime &lifetime() {
        return _lifetime;
    }
    void cancel() {
        _stats->cancelled = true;
        finished = true;
        if (_stats->callbackActive) *_documentCancelled = true;
    }
    LoaderStats *_stats = nullptr;
    bool *_documentCancelled = nullptr;
    Lifetime _lifetime;
    bool finished = false;
};
struct DocumentData {
    DocumentData(uint64 generation, bool ordinaryDemand)
    : _loaderGeneration(generation)
    , _hasOrdinarySaveDemand(ordinaryDemand)
    , _owner(&_ownerStorage) {
        _loader = std::make_unique<Loader>(&_stats, &_cancelled);
    }
    bool loading() const {
        return bool(_loader);
    }
    bool cancelled() const {
        return _cancelled;
    }
    void cancel() {
        _cancelled = true;
        const auto loader = base::take(_loader);
        if (_cancelled) loader->cancel();
        _owner->documentLoadDone(this);
    }
    bool cancelSharedDownloadIfUnused(uint64 generation) {''' + cancel + r'''}
    uint64 _loaderGeneration = 0;
    bool _hasOrdinarySaveDemand = false;
    bool _cancelled = false;
    LoaderStats _stats;
    std::unique_ptr<Loader> _loader;
    Owner _ownerStorage;
    Owner *_owner = nullptr;
};
int main() {
    auto document = DocumentData(11, false);
    if (!document.cancelSharedDownloadIfUnused(11)) return 1;
    if (document.loading() || document.cancelled()) return 2;
    if (!document._stats.cancelled || document._stats.callbackActive) return 3;
    return document._owner->doneCalls == 1 ? 0 : 4;
}
'''
        compile_and_run(cpp)

    def test_ordinary_save_joining_auxiliary_loader_prevents_ayu_cancellation(self):
        document = (SOURCE / "data/data_document.cpp").read_text()
        save = block_body(document, "void DocumentData::save(")
        loader_creation = block_body(
            save,
            "else {\n\t\tstatus = FileReady;",
        )
        destination = "if (destination) {\n\t\tif (!_loader->setDestinationFile"
        destination_block = block_body(save, destination)
        destination_failure = destination_block.index("return;")
        ordinary_demand = save.index(
            "if (_loader && request == SaveRequest::Ordinary)")
        self.assertIn("_hasOrdinarySaveDemand = true", save)
        self.assertNotIn("_hasOrdinarySaveDemand = true", destination_block)
        self.assertRegex(
            save,
            r"(?m)^\tif \(_loader && request == SaveRequest::Ordinary\) \{\n"
            r"\t\t_hasOrdinarySaveDemand = true;\n\t\}",
        )
        destination_start = save.index(destination)
        destination_header = "if (destination) {"
        demand_offset = ordinary_demand - destination_start - len(destination_header)
        self.assertGreater(demand_offset, len(destination_block))
        self.assertGreater(demand_offset, destination_failure)
        self.assertIn("createdLoader = true;", loader_creation)
        self.assertIn("if (++_loaderGeneration == 0)", loader_creation)
        self.assertIn("_hasOrdinarySaveDemand = false;", loader_creation)
        self.assertEqual(save.count("if (++_loaderGeneration == 0)"), 1)
        self.assertEqual(save.count("_hasOrdinarySaveDemand = false;"), 1)
        ordinary_demand = block_body(
            save,
            "if (_loader && request == SaveRequest::Ordinary)",
        )
        self.assertEqual(ordinary_demand.strip(), "_hasOrdinarySaveDemand = true;")
        self.assertLess(
            save.index("if (_loader && request == SaveRequest::Ordinary)"),
            save.index("if (createdLoader) {\n\t\thandleLoaderUpdates()"),
        )

        cancel_shared = block_body(
            document,
            "bool DocumentData::cancelSharedDownloadIfUnused(",
        )
        self.assertLess(
            cancel_shared.index("CanCancelSharedDocumentLoad("),
            cancel_shared.index("const auto loader = base::take(_loader);"),
        )

        download = (
            SOURCE / "ayu/features/forward/ayu_sync_download.cpp").read_text()
        start = block_body(
            download,
            "QString loadDocumentSync(\n\tconst base::weak_ptr<Main::Session> &session,",
        )
        cancel = block_body(download, "void CancelDocumentDownload(")
        self.assertIn("DocumentData::SaveRequest::Shared", start)
        self.assertIn("state->loaderGeneration = data->loaderGeneration()", start)
        self.assertIn("cancelSharedDownloadIfUnused(", cancel)
        self.assertIn("loaderGeneration", cancel)
        self.assertNotIn("document->cancel()", cancel)

    def test_automatic_media_load_registers_ordinary_cloud_demand(self):
        media = (SOURCE / "data/data_document_media.cpp").read_text()
        document = (SOURCE / "data/data_document.cpp").read_text()
        document_header = (SOURCE / "data/data_document.h").read_text()
        self.assertIn(
            "SaveRequest request = SaveRequest::Ordinary",
            document_header,
        )
        automatic_load = block_body(
            media,
            "void DocumentMedia::automaticLoad(",
        )
        save = block_body(document, "void DocumentData::save(")
        ordinary_demand_start = save.index(
            "if (_loader && request == SaveRequest::Ordinary)")
        ordinary_demand_end = save.index(
            "if (createdLoader) {\n\t\thandleLoaderUpdates()",
            ordinary_demand_start,
        )
        ordinary_demand = save[
            ordinary_demand_start:ordinary_demand_end].strip()
        start_loader = block_body(
            document,
            "if (createdLoader && loading())",
        )
        cpp = r'''#include <string>
using DataFileOrigin = int;
struct QString {
    QString() = default;
    QString(const char *value) : value(value) {}
    bool isEmpty() const { return value.empty(); }
    std::string value;
};
enum class LoadFromCloudSetting { LocalOnly, CloudOrLocal };
constexpr auto LoadFromLocalOnly = LoadFromCloudSetting::LocalOnly;
constexpr auto LoadFromCloudOrLocal = LoadFromCloudSetting::CloudOrLocal;
enum class SaveRequest { Ordinary, Shared };
enum class FileStatus { Ready, Missing };
constexpr auto FileReady = FileStatus::Ready;
struct History {
    int peer = 0;
};
struct HistoryItem {
    History *history() const { return historyValue; }
    History *historyValue = nullptr;
};
struct Settings {
    int autoDownload() const { return 0; }
};
struct Session {
    Settings settings() const { return {}; }
};
struct DocumentData {
    bool loaded() const { return loadedValue; }
    bool uploading() const { return uploadingValue; }
    bool cancelled() const { return cancelledValue; }
    bool sticker() const { return stickerValue; }
    bool isAnimation() const { return animationValue; }
    bool saveToCache() const { return saveToCacheValue; }
    bool forbidsFileSave() const { return forbidsFileSaveValue; }
    QString filename() const { return filenameValue; }
    Session &session() { return sessionValue; }
    bool loading() const { return loadingValue; }
    void permitLoadFromCloud() { ++permitCloudCalls; }
    void start() { ++startCalls; }
    void save(
            DataFileOrigin,
            QString filename,
            LoadFromCloudSetting fromCloud,
            bool autoLoading,
            SaveRequest request = SaveRequest::Ordinary) {
        ++saveCalls;
        savedFilename = filename;
        savedFromCloud = fromCloud;
        savedAutoLoading = autoLoading;
        const auto createdLoader = !loadingValue;
        loadingValue = true;
        const auto _loader = loadingValue ? this : nullptr;
''' + ordinary_demand + r'''
        if (createdLoader && loading()) {
''' + start_loader + r'''
        }
    }
    FileStatus status = FileReady;
    bool loadedValue = false;
    bool uploadingValue = false;
    bool cancelledValue = false;
    bool stickerValue = false;
    bool animationValue = true;
    bool saveToCacheValue = true;
    bool forbidsFileSaveValue = false;
    bool loadingValue = false;
    bool _hasOrdinarySaveDemand = false;
    int saveCalls = 0;
    int permitCloudCalls = 0;
    int startCalls = 0;
    QString filenameValue;
    QString savedFilename;
    LoadFromCloudSetting savedFromCloud = LoadFromLocalOnly;
    bool savedAutoLoading = false;
    Session sessionValue;
};
namespace Core {
enum class NameType { Other, Executable };
struct Application {
    bool canSaveFileWithoutAskingForPath() const { return true; }
};
Application &App() {
    static auto app = Application();
    return app;
}
NameType DetectNameType(const QString &) { return NameType::Other; }
}
namespace Data::AutoDownload {
bool CloudAllowed = true;
template <typename... Args>
bool Should(Args &&...) { return CloudAllowed; }
}
QString DocumentFileNameForSave(DocumentData *) { return QString("save.mov"); }
class DocumentMedia {
public:
    explicit DocumentMedia(DocumentData *owner) : _owner(owner) {}
    bool loaded() const { return _owner->loaded(); }
    void automaticLoad(DataFileOrigin origin, const HistoryItem *item);
private:
    DocumentData *_owner = nullptr;
};
void DocumentMedia::automaticLoad(
        DataFileOrigin origin,
        const HistoryItem *item) {''' + automatic_load + r'''}
int main() {
    auto activeCloud = DocumentData();
    activeCloud.loadingValue = true;
    auto cloudMedia = DocumentMedia(&activeCloud);
    Data::AutoDownload::CloudAllowed = true;
    cloudMedia.automaticLoad(7, nullptr);
    if (activeCloud.saveCalls != 1
        || activeCloud.permitCloudCalls != 0
        || activeCloud.startCalls != 0
        || !activeCloud._hasOrdinarySaveDemand
        || !activeCloud.savedFilename.isEmpty()
        || activeCloud.savedFromCloud != LoadFromCloudOrLocal
        || !activeCloud.savedAutoLoading) {
        return 1;
    }

    auto activeLocal = DocumentData();
    activeLocal.loadingValue = true;
    auto localMedia = DocumentMedia(&activeLocal);
    Data::AutoDownload::CloudAllowed = false;
    localMedia.automaticLoad(8, nullptr);
    if (activeLocal.saveCalls != 0
        || activeLocal.permitCloudCalls != 0
        || activeLocal.startCalls != 0
        || activeLocal._hasOrdinarySaveDemand) {
        return 2;
    }

    auto idleCloud = DocumentData();
    auto idleMedia = DocumentMedia(&idleCloud);
    Data::AutoDownload::CloudAllowed = true;
    idleMedia.automaticLoad(9, nullptr);
    if (idleCloud.saveCalls != 1
        || !idleCloud.loadingValue
        || !idleCloud._hasOrdinarySaveDemand
        || idleCloud.startCalls != 1
        || idleCloud.savedFromCloud != LoadFromCloudOrLocal
        || !idleCloud.savedAutoLoading) {
        return 3;
    }
    return 0;
}
'''
        compile_and_run(cpp)

    def test_empty_file_name_does_not_retarget_active_loader(self):
        source = (SOURCE / "storage/file_download.cpp").read_text()
        set_file_name = block_body(source, "bool FileLoader::setFileName(")
        cpp = r'''#include <string>
enum class LoadTo { LoadToCacheAsWell, LoadToFileOnly };
constexpr auto LoadToCacheAsWell = LoadTo::LoadToCacheAsWell;
struct QString {
    QString(const char *value = "") : value(value) {}
    bool isEmpty() const { return value.empty(); }
    friend bool operator==(const QString &, const QString &) = default;
    std::string value;
};
struct File {
    void setFileName(const QString &value) { name = value; }
    QString name;
};
struct FileLoader {
    bool setFileName(const QString &fileName);
    LoadTo _toCache = LoadTo::LoadToCacheAsWell;
    QString _filename;
    File _file;
};
bool FileLoader::setFileName(const QString &fileName) {''' + set_file_name + r'''}
int main() {
    auto active = FileLoader();
    active._filename = QString("download.mp4");
    active._file.setFileName(active._filename);
    if (!active.setFileName(QString())) return 1;
    if (!(active._filename == QString("download.mp4"))) return 2;
    if (!(active._file.name == QString("download.mp4"))) return 3;
    if (active.setFileName(QString("other.mp4"))) return 4;

    auto unset = FileLoader();
    if (!unset.setFileName(QString("download.mp4"))) return 5;
    return (unset._filename == QString("download.mp4")
        && unset._file.name == QString("download.mp4")) ? 0 : 6;
}
'''
        compile_and_run(cpp)

    def test_owner_cleanup_preserves_started_shared_download(self):
        source = (SOURCE / "ayu/features/forward/ayu_sync_download.cpp").read_text()
        load_document = block_body(
            source,
            "QString loadDocumentSync(\n\tconst base::weak_ptr<Main::Session> &session,",
        )
        cleanup = block_body(
            load_document,
            "const auto cleanup = gsl::finally([&]",
        )
        self.assertIn("registration.state->started", cleanup)
        self.assertIn("if (!started)", cleanup)
        self.assertIn("FinishDocumentDownload(", cleanup)
        self.assertNotIn("CancelDocumentDownload(", cleanup)

    def test_deferred_borrowed_callback_rechecks_current_loader(self):
        source = (
            SOURCE / "ayu/features/forward/ayu_sync_download.cpp").read_text()
        watch = block_body(source, "[[nodiscard]] bool WatchExistingDocumentLoad(")
        callback = block_body(
            watch,
            "| rpl::on_next([=](not_null<DocumentData*>) {",
        )
        cpp = r'''#include <atomic>
#include <functional>
#include <memory>
#include <mutex>
#include <string>
#include <vector>
using int64 = long long;
template <typename Type>
struct not_null {
    explicit not_null(Type value) : value(value) {}
    Type operator->() const { return value; }
    Type get() const { return value; }
    Type value;
};
namespace crl {
std::vector<std::function<void()>> MainQueue;
template <typename Callback>
void on_main(Callback callback) {
    MainQueue.emplace_back(std::move(callback));
}
void DrainMainQueue() {
    while (!MainQueue.empty()) {
        auto callback = std::move(MainQueue.front());
        MainQueue.erase(MainQueue.begin());
        callback();
    }
    }
}
struct DocumentData {
    int id = 1;
    bool loading = false;
    std::string path = "old";
    std::string loadingPath = "old";
    std::string filepath(bool) const { return path; }
};
struct SessionData {
    explicit SessionData(DocumentData *document) : documentValue(document) {}
    not_null<DocumentData*> document(int) const {
        return not_null<DocumentData*>(documentValue);
    }
    DocumentData *documentValue = nullptr;
};
struct Session {
    explicit Session(DocumentData *document) : dataValue(document) {}
    SessionData &data() { return dataValue; }
    SessionData dataValue;
};
struct WeakSession {
    Session *value = nullptr;
    Session *get() const { return value; }
};
struct DownloadState {
    std::mutex mutex;
    std::atomic<bool> finished = false;
    std::string path = "old";
};
struct WeakState {
    std::weak_ptr<DownloadState> value;
    std::shared_ptr<DownloadState> lock() const { return value.lock(); }
};
struct DownloadKey { int documentId = 0; };
int RewatchCalls = 0;
int CompletionCalls = 0;
bool WatchExistingDocumentLoad(
        Session *,
        DownloadKey,
        int64,
        const std::shared_ptr<DownloadState> &state,
        not_null<DocumentData*> document) {
    if (document->loading) {
        const auto lock = std::lock_guard(state->mutex);
        state->path = document->loadingPath;
        ++RewatchCalls;
        return true;
    }
    return false;
}
void CompleteDocumentDownload(
        Session *,
        DownloadKey,
        int64,
        const std::shared_ptr<DownloadState> &) {
    ++CompletionCalls;
}
void ReconcileDocumentDownloadAfterProgress(
        WeakSession weakSession,
        DownloadKey key,
        int64 expectedSize,
        WeakState weakState) {
    crl::on_main([=] {
        const auto current = weakSession.get();
        const auto state = weakState.lock();
        if (!current || !state
            || state->finished.load(std::memory_order_acquire)) {
            return;
        }
        const auto document = current->data().document(key.documentId);
        if (WatchExistingDocumentLoad(
                current, key, expectedSize, state, document)) {
            return;
        }
        CompleteDocumentDownload(current, key, expectedSize, state);
    });
}
int main() {
    auto document = DocumentData();
    auto session = Session(&document);
    auto state = std::make_shared<DownloadState>();
    const auto weakSession = WeakSession{ &session };
    const auto weakState = WeakState{ state };
    const auto key = DownloadKey{ 1 };
    const auto expectedSize = int64(10);
    auto callback = [=](not_null<DocumentData*>) {''' + callback + r'''};

    callback(not_null<DocumentData*>(&document));
    if (crl::MainQueue.size() != 1) return 1;
    document.loading = true;
    document.loadingPath = "replacement";
    crl::DrainMainQueue();
    if (RewatchCalls != 1
        || CompletionCalls != 0
        || state->path != "replacement") {
        return 2;
    }

    RewatchCalls = 0;
    CompletionCalls = 0;
    callback(not_null<DocumentData*>(&document));
    if (crl::MainQueue.size() != 1) return 3;
    state->finished.store(true, std::memory_order_release);
    crl::DrainMainQueue();
    if (RewatchCalls != 0 || CompletionCalls != 0) return 4;
    return 0;
}
'''
        compile_and_run(cpp)

    def test_last_consumer_cleanup_keeps_current_document_path(self):
        source = (SOURCE / "ayu/features/forward/ayu_sync_download.cpp").read_text()
        cancel = block_body(source, "void CancelDocumentDownload(")
        self.assertIn("const auto loadingPath = document->loadingFilePath();", cancel)
        self.assertIn("const auto loadedPath = document->filepath(true);", cancel)
        self.assertIn("SameFilePath(path, loadingPath)", cancel)
        self.assertIn("SameFilePath(path, loadedPath)", cancel)
        self.assertIn("removePath = !pathIsCurrent;", cancel)
        cancel_shared = cancel.index("document->cancelSharedDownloadIfUnused(")
        self.assertIn("loaderGeneration", cancel[cancel_shared:cancel_shared + 120])
        self.assertIn("TransferDocumentDownloadLoaderOwnership(",
                      cancel[:cancel_shared])
        self.assertIn("!replacementKeepsLoader", cancel[:cancel_shared])

    def test_last_consumer_remains_responsible_for_cancellation(self):
        source = (SOURCE / "ayu/features/forward/ayu_sync_download.cpp").read_text()
        release_consumer = block_body(
            source,
            "void ReleaseDocumentDownloadConsumer(\n\t\tDocumentDownloadKey key,",
        )
        self.assertIn("--state->consumers", release_consumer)
        self.assertIn("if (state->consumers == 0)", release_consumer)
        self.assertIn("CancelDocumentDownload(key, state)", release_consumer)

    def test_replacement_borrower_inherits_the_last_consumers_loader(self):
        source = (SOURCE / "ayu/features/forward/ayu_sync_download.cpp").read_text()
        register = block_body(
            source,
            "DocumentDownloadRegistration RegisterDocumentDownload(",
        )
        finish = block_body(
            source,
            "[[nodiscard]] bool FinishDocumentDownload(",
        )
        replaced = block_body(
            source,
            "ReplacedDocumentDownload(",
        )
        watch = block_body(
            source,
            "[[nodiscard]] bool WatchExistingDocumentLoad(",
        )
        transfer = block_body(
            source,
            "[[nodiscard]] bool TransferDocumentDownloadLoaderOwnership(",
        )
        cancel = block_body(source, "void CancelDocumentDownload(")
        release = block_body(
            source,
            "void ReleaseDocumentDownloadConsumer(\n\t\tDocumentDownloadKey key,",
        )
        cpp = r'''#include <atomic>
#include <cassert>
#include <compare>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <functional>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <utility>
#include <vector>
using uint64 = std::uint64_t;
using int64 = std::int64_t;
struct DocumentData;
template <typename Type>
struct not_null;
namespace rpl {
struct producer {};
struct lifetime {
    void destroy() { destroyed = true; }
    bool destroyed = false;
};
struct filter_operation {};
struct take_operation {};
template <typename Predicate>
filter_operation filter(Predicate) { return {}; }
take_operation take(int) { return {}; }
}
struct QString {
    QString() = default;
    QString(const char *value) : value(value) {}
    bool isEmpty() const { return value.empty(); }
    void clear() { value.clear(); }
    friend bool operator==(const QString &, const QString &) = default;
    std::string value;
};
struct DocumentDownloadKey {
    uint64 sessionUniqueId = 0;
    uint64 documentId = 0;
    friend auto operator<=>(DocumentDownloadKey, DocumentDownloadKey) = default;
};
namespace Main { struct Session; }
namespace Data { struct FileOrigin {}; }
namespace base {
template <typename Key, typename Value>
using flat_map = std::map<Key, Value>;
template <typename Type>
struct weak_ptr {
    Type *value = nullptr;
    Type *get() const { return value; }
};
}
namespace rpl {
std::vector<std::function<void(not_null<DocumentData*>)>> &Subscriptions() {
    static auto subscriptions =
        std::vector<std::function<void(not_null<DocumentData*>)>>();
    return subscriptions;
}
template <typename Callback>
struct on_next_operation {};
template <typename Callback>
on_next_operation<Callback> on_next(Callback callback, lifetime &) {
    Subscriptions().push_back(std::move(callback));
    return {};
}
}
struct TimedCountDownLatch {
    explicit TimedCountDownLatch(int) {}
    void countDown() { ++calls; }
    int calls = 0;
};
template <typename Type>
struct not_null {
    not_null(Type value) : value(value) {}
    Type operator->() const { return value; }
    Type value;
};
struct Pipeline {};
template <typename Left, typename Right>
Pipeline operator|(Left &&, Right &&) { return {}; }
struct DocumentData {
    uint64 id = 8;
    bool loading() const { return loadingValue; }
    QString loadingFilePath() const {
        return loadingValue ? loadingPath : QString();
    }
    QString filepath(bool) const { return loadedPath; }
    uint64 loaderGeneration() const { return generation; }
    bool cancelSharedDownloadIfUnused(uint64 requestedGeneration) {
        if (!loadingValue
            || requestedGeneration != generation
            || ordinaryDemand) {
            return false;
        }
        loadingValue = false;
        cancelledValue = true;
        ++cancelCalls;
        return true;
    }
    bool loadingValue = true;
    bool ordinaryDemand = false;
    bool cancelledValue = false;
    int cancelCalls = 0;
    uint64 generation = 17;
    QString loadingPath = QString("shared-part.mp4");
    QString loadedPath;
};
struct SessionData {
    explicit SessionData(DocumentData *document) : documentValue(document) {}
    not_null<DocumentData*> documentFor(uint64) const {
        return not_null<DocumentData*>(documentValue);
    }
    not_null<DocumentData*> document(uint64 id) const {
        return documentFor(id);
    }
    rpl::producer documentLoadProgress() const { return {}; }
    DocumentData *documentValue = nullptr;
};
namespace Main {
struct Session {
    explicit Session(DocumentData *document) : dataValue(document) {}
    SessionData &data() { return dataValue; }
    SessionData dataValue;
};
}
namespace crl {
struct MainCall {
    std::function<void()> callback;
    bool completed = false;
};
std::deque<std::shared_ptr<MainCall>> MainQueue;
std::mutex MainQueueMutex;
std::condition_variable MainQueueCondition;
std::thread::id MainThread;
bool PauseNextSynchronousCall = false;
bool SynchronousCallPaused = false;
bool ResumeSynchronousCall = false;
void WaitForPausedSynchronousCall() {
    auto lock = std::unique_lock(MainQueueMutex);
    MainQueueCondition.wait(lock, [] {
        return SynchronousCallPaused;
    });
}
void ResumePausedSynchronousCall() {
    const auto lock = std::lock_guard(MainQueueMutex);
    ResumeSynchronousCall = true;
    MainQueueCondition.notify_all();
}
void WaitForPendingMainCall() {
    auto lock = std::unique_lock(MainQueueMutex);
    MainQueueCondition.wait(lock, [] {
        return !MainQueue.empty();
    });
}
template <typename Callback>
void on_main(Callback callback) {
    const auto call = std::make_shared<MainCall>();
    call->callback = std::move(callback);
    const auto lock = std::lock_guard(MainQueueMutex);
    MainQueue.push_back(call);
    MainQueueCondition.notify_all();
}
template <typename Callback>
void on_main_sync(Callback callback) {
    if (std::this_thread::get_id() == MainThread) {
        callback();
        return;
    }
    const auto call = std::make_shared<MainCall>();
    call->callback = std::move(callback);
    auto lock = std::unique_lock(MainQueueMutex);
    if (PauseNextSynchronousCall) {
        PauseNextSynchronousCall = false;
        SynchronousCallPaused = true;
        MainQueueCondition.notify_all();
        MainQueueCondition.wait(lock, [] {
            return ResumeSynchronousCall;
        });
        ResumeSynchronousCall = false;
    }
    MainQueue.push_back(call);
    MainQueueCondition.notify_all();
    MainQueueCondition.wait(lock, [&] {
        return call->completed;
    });
}
void DrainMainQueue() {
    while (true) {
        auto call = std::shared_ptr<MainCall>();
        {
            const auto lock = std::lock_guard(MainQueueMutex);
            if (MainQueue.empty()) {
                return;
            }
            call = MainQueue.front();
            MainQueue.pop_front();
        }
        call->callback();
        {
            const auto lock = std::lock_guard(MainQueueMutex);
            call->completed = true;
        }
        MainQueueCondition.notify_all();
    }
}
}
struct ActiveDocumentDownload {
    QString path;
    QString readyPath;
    TimedCountDownLatch done{ 1 };
    rpl::lifetime lifetime;
    base::weak_ptr<Main::Session> session;
    std::mutex mutex;
    int consumers = 1;
    Data::FileOrigin origin;
    bool allowSizeFallback = false;
    bool started = false;
    bool ownsLoader = false;
    uint64 loaderGeneration = 0;
    std::atomic_bool finished = false;
};
bool DocumentReady(not_null<DocumentData*>, const QString &, int64) {
    return false;
}
void CompleteDocumentDownload(
        not_null<Main::Session*>,
        DocumentDownloadKey,
        int64,
        const std::shared_ptr<ActiveDocumentDownload> &) {}
struct DocumentDownloadRegistration {
    std::shared_ptr<ActiveDocumentDownload> state;
    bool owner = false;
};
using ActiveDocumentDownloads = base::flat_map<
    DocumentDownloadKey,
    std::shared_ptr<ActiveDocumentDownload>>;
std::mutex &DocumentDownloadsMutex() {
    static auto mutex = std::mutex();
    return mutex;
}
ActiveDocumentDownloads &DocumentDownloads() {
    static auto downloads = ActiveDocumentDownloads();
    return downloads;
}
bool SameFilePath(const QString &left, const QString &right) {
    return left == right;
}
struct QFile {
    static bool remove(const QString &) { return true; }
};
#define Expects(condition) assert(condition)
DocumentDownloadRegistration RegisterDocumentDownload(
    DocumentDownloadKey key,
    base::weak_ptr<Main::Session> weakSession) {''' + register + r'''}
bool FinishDocumentDownload(
    DocumentDownloadKey key,
    const std::shared_ptr<ActiveDocumentDownload> &state,
    bool ready,
    bool removePath = true,
    QString *finishedPath = nullptr) {''' + finish + r'''}
std::shared_ptr<ActiveDocumentDownload> ReplacedDocumentDownload(
    DocumentDownloadKey key,
    const std::shared_ptr<ActiveDocumentDownload> &state) {''' + replaced + r'''}
bool TransferDocumentDownloadLoaderOwnership(
    const std::shared_ptr<ActiveDocumentDownload> &state,
    const std::shared_ptr<ActiveDocumentDownload> &replacement,
    uint64 loaderGeneration,
    const QString &path) {''' + transfer + r'''}
void CancelDocumentDownload(
    DocumentDownloadKey key,
    const std::shared_ptr<ActiveDocumentDownload> &state) {''' + cancel + r'''}
void ReleaseDocumentDownloadConsumer(
    DocumentDownloadKey key,
    const std::shared_ptr<ActiveDocumentDownload> &state) {''' + release + r'''}
void ReconcileDocumentDownloadAfterProgress(
    const base::weak_ptr<Main::Session> &,
    DocumentDownloadKey,
    int64,
    const std::weak_ptr<ActiveDocumentDownload> &);
bool WatchExistingDocumentLoad(
    not_null<Main::Session*> session,
    DocumentDownloadKey key,
    int64 expectedSize,
    const std::shared_ptr<ActiveDocumentDownload> &state,
    not_null<DocumentData*> document) {''' + watch + r'''}
void ReconcileDocumentDownloadAfterProgress(
    const base::weak_ptr<Main::Session> &,
    DocumentDownloadKey,
    int64,
    const std::weak_ptr<ActiveDocumentDownload> &) {}
int main() {
    crl::MainThread = std::this_thread::get_id();
    auto document = DocumentData();
    auto session = Main::Session(&document);
    const auto key = DocumentDownloadKey{ 3, 8 };
    auto transferOwner = std::make_shared<ActiveDocumentDownload>();
    auto transferReplacement = std::make_shared<ActiveDocumentDownload>();
    transferOwner->path = QString("shared-part.mp4");
    transferOwner->ownsLoader = true;
    transferOwner->loaderGeneration = 17;
    transferReplacement->path = QString("shared-part.mp4");
    transferReplacement->started = true;
    transferReplacement->loaderGeneration = 18;
    if (TransferDocumentDownloadLoaderOwnership(
            transferOwner,
            transferReplacement,
            17,
            transferOwner->path)
        || !transferOwner->ownsLoader
        || transferReplacement->ownsLoader) {
        return 10;
    }
    transferReplacement->loaderGeneration = 17;
    transferReplacement->path = QString("different-part.mp4");
    if (TransferDocumentDownloadLoaderOwnership(
            transferOwner,
            transferReplacement,
            17,
            transferOwner->path)
        || !transferOwner->ownsLoader
        || transferReplacement->ownsLoader) {
        return 11;
    }
    transferReplacement->path = transferOwner->path;
    if (!TransferDocumentDownloadLoaderOwnership(
            transferOwner,
            transferReplacement,
            17,
            transferOwner->path)
        || transferOwner->ownsLoader
        || !transferReplacement->ownsLoader) {
        return 12;
    }
    auto old = std::make_shared<ActiveDocumentDownload>();
    old->session = base::weak_ptr<Main::Session>{ &session };
    old->path = QString("shared-part.mp4");
    old->started = true;
    old->ownsLoader = true;
    old->loaderGeneration = 17;
    DocumentDownloads().emplace(key, old);

    {
        const auto lock = std::lock_guard(crl::MainQueueMutex);
        crl::PauseNextSynchronousCall = true;
    }
    auto releaseThread = std::thread([&] {
        ReleaseDocumentDownloadConsumer(key, old);
    });
    crl::WaitForPausedSynchronousCall();

    auto registration = DocumentDownloadRegistration();
    auto watchResult = false;
    auto registrationMutex = std::mutex();
    auto registrationCondition = std::condition_variable();
    auto registrationReady = false;
    auto registrationThread = std::thread([&] {
        registration = RegisterDocumentDownload(
            key,
            base::weak_ptr<Main::Session>{ &session });
        {
            const auto lock = std::lock_guard(registrationMutex);
            registrationReady = true;
        }
        registrationCondition.notify_one();
        if (registration.owner) {
            crl::on_main_sync([&] {
                watchResult = WatchExistingDocumentLoad(
                    not_null<Main::Session*>(&session),
                    key,
                    10,
                    registration.state,
                    not_null<DocumentData*>(&document));
            });
        }
    });
    {
        auto lock = std::unique_lock(registrationMutex);
        registrationCondition.wait(lock, [&] {
            return registrationReady;
        });
    }
    if (registration.owner) {
        crl::WaitForPendingMainCall();
        crl::DrainMainQueue();
    }
    registrationThread.join();
    if (!registration.owner
        || !watchResult
        || registration.state->path != document.loadingFilePath()
        || !registration.state->started
        || registration.state->ownsLoader
        || registration.state->loaderGeneration != document.generation
        || rpl::Subscriptions().size() != 1) {
        crl::ResumePausedSynchronousCall();
        crl::WaitForPendingMainCall();
        crl::DrainMainQueue();
        releaseThread.join();
        return 13;
    }

    crl::ResumePausedSynchronousCall();
    crl::WaitForPendingMainCall();
    crl::DrainMainQueue();
    releaseThread.join();
    if (!document.loadingValue || document.cancelCalls) return 2;

    ReleaseDocumentDownloadConsumer(key, registration.state);
    return (document.cancelledValue
        && document.cancelCalls == 1
        && !document.loadingValue) ? 0 : 3;
}
'''
        compile_and_run(cpp)
        watch = block_body(
            source,
            "[[nodiscard]] bool WatchExistingDocumentLoad(",
        )
        self.assertIn(
            "state->loaderGeneration = document->loaderGeneration()",
            watch,
        )

    def test_regular_load_is_borrowed_without_retargeting_or_cancelling_it(self):
        source = (SOURCE / "ayu/features/forward/ayu_sync_download.cpp").read_text()
        load_document = block_body(
            source,
            "QString loadDocumentSync(\n\tconst base::weak_ptr<Main::Session> &session,",
        )
        watch_existing = block_body(
            source,
            "bool WatchExistingDocumentLoad(",
        )
        complete = block_body(
            source,
            "void CompleteDocumentDownload(",
        )
        cancel = block_body(
            source,
            "void CancelDocumentDownload(",
        )
        self.assertNotIn("if (data->loading())", load_document)
        self.assertLess(
            load_document.rindex("WatchExistingDocumentLoad("),
            load_document.index("data->save("),
        )
        borrowed_wait = block_body(
            load_document,
            "if (watchingExistingLoad) {",
        )
        self.assertIn("WaitForDownload(", borrowed_wait)
        self.assertIn("if (waitResult != DownloadWaitResult::Completed)",
                      borrowed_wait)
        self.assertIn("return ReadyDocumentPath(registration.state, document.size)",
                      borrowed_wait)
        ready = block_body(watch_existing, "if (DocumentReady(")
        self.assertIn("state->path = document->filepath(true)", ready)
        self.assertIn("FinishDocumentDownload(key, state, true, false)", ready)
        self.assertIn("state->ownsLoader = false", watch_existing)
        self.assertIn("state->path = document->loadingFilePath()", watch_existing)
        self.assertLess(
            watch_existing.index("if (!document->loading())"),
            watch_existing.index("documentLoadProgress()"),
        )
        completion_filter = block_body(
            watch_existing,
            "| rpl::filter([=](not_null<DocumentData*> changed) {",
        )
        self.assertIn("changed->id == key.documentId", completion_filter)
        self.assertIn("!changed->loading()", completion_filter)
        self.assertIn("| rpl::take(1)", watch_existing)
        callback = block_body(
            watch_existing,
            "| rpl::on_next([=](not_null<DocumentData*>) {",
        )
        self.assertIn("ReconcileDocumentDownloadAfterProgress(", callback)
        reconcile = block_body(
            source[source.rindex("void ReconcileDocumentDownloadAfterProgress("):],
            "void ReconcileDocumentDownloadAfterProgress(",
        )
        self.assertIn("crl::on_main([=]", reconcile)
        self.assertIn("WatchExistingDocumentLoad(", reconcile)
        self.assertIn("document->filepath(true)", reconcile)
        self.assertIn("CompleteDocumentDownload(", reconcile)
        self.assertIn("FinishDocumentDownload(key, state, ready, ownsLoader)", complete)
        self.assertIn("document->status != FileDownloadFailed", complete)
        self.assertIn("!document->cancelled()", complete)
        self.assertIn("DocumentReady(document, path, expectedSize)", complete)
        borrowed_cancel = block_body(cancel, "if (ownsLoader) {")
        self.assertRegex(
            borrowed_cancel,
            r"cancelSharedDownloadIfUnused\(\s*loaderGeneration\)",
        )
        self.assertIn("auto removePath = ownsLoader;", cancel)
        self.assertIn("if (removePath && !path.isEmpty())", cancel)

    def test_cache_only_borrowed_load_materializes_a_verified_file(self):
        source = (SOURCE / "ayu/features/forward/ayu_sync_download.cpp").read_text()
        complete = block_body(source, "void CompleteDocumentDownload(")
        materialize_signature = "[[nodiscard]] bool BeginDocumentPathMaterialization("
        materialize = block_body(
            source[source.rindex(materialize_signature):],
            materialize_signature,
        )
        self.assertIn("!ready", complete)
        self.assertIn("&& !ownsLoader", complete)
        self.assertIn("document->status != FileDownloadFailed", complete)
        self.assertIn("!document->cancelled()", complete)
        self.assertIn("BeginDocumentPathMaterialization(", complete)
        self.assertIn("NextDocumentPath(weakSession, key.documentId)", materialize)
        self.assertIn("QIODevice::NewOnly", materialize)
        self.assertIn("state->path = path", materialize)
        self.assertIn("state->ownsLoader = true", materialize)
        self.assertIn("origin = state->origin", materialize)
        self.assertIn("document->save(", materialize)
        self.assertIn("DocumentData::SaveRequest::Shared", materialize)
        self.assertLess(
            materialize.index("document->save("),
            materialize.index("state->loaderGeneration = document->loaderGeneration()"),
        )
        self.assertIn("state->allowSizeFallback = !document->loading()", materialize)
        self.assertIn("documentLoadProgress()", materialize)
        self.assertIn("return changed->id == key.documentId", materialize)
        self.assertIn("&& !changed->loading()", materialize)

    def test_owner_completion_follows_replacement_and_retries_ui_cancel(self):
        source = (SOURCE / "ayu/features/forward/ayu_sync_download.cpp").read_text()
        reconcile_signature = "void ReconcileDocumentDownloadAfterProgress("
        reconcile = block_body(
            source[source.rindex(reconcile_signature):],
            reconcile_signature,
        )
        watch = block_body(source, "bool WatchExistingDocumentLoad(")
        materialize_signature = "[[nodiscard]] bool BeginDocumentPathMaterialization("
        materialize = block_body(source[source.rindex(materialize_signature):],
                                 materialize_signature)
        load_document = block_body(
            source,
            "QString loadDocumentSync(\n\tconst base::weak_ptr<Main::Session> &session,",
        )
        self.assertIn("crl::on_main([=]", reconcile)
        self.assertIn("state->finished.load(std::memory_order_acquire)", reconcile)
        self.assertIn("WatchExistingDocumentLoad(", reconcile)
        self.assertIn(
            "state->loaderGeneration != document->loaderGeneration()",
            reconcile,
        )
        self.assertIn("state->ownsLoader = false", reconcile)
        self.assertIn("BeginDocumentPathMaterialization(", reconcile)
        self.assertIn("document->cancelled()", reconcile)
        for owner in (watch, materialize, load_document):
            self.assertIn("ReconcileDocumentDownloadAfterProgress(", owner)

        cpp = r'''#include <atomic>
#include <cstdint>
#include <deque>
#include <functional>
#include <memory>
#include <mutex>
#include <string>
#include <utility>
#include <vector>

using int64 = std::int64_t;
using uint64 = std::uint64_t;
template <typename Type>
using not_null = Type;

constexpr auto FileDownloadFailed = 1;

struct DocumentDownloadKey {
    int documentId = 0;
};

struct ActiveDocumentDownload {
    std::mutex mutex;
    std::atomic<bool> finished = false;
    std::string path;
    uint64 loaderGeneration = 0;
    bool allowSizeFallback = false;
    bool ownsLoader = true;
    int doneCount = 0;
    bool success = false;
    bool lastFinishWouldRemovePath = true;
};

struct DocumentData {
    int id = 0;
    int status = 0;
    bool loadingValue = false;
    bool cancelledValue = false;
    bool readyValue = false;
    uint64 generation = 0;
    std::string loadingPath;
    std::string loadedPath;
    int64 size = 10;
    int materializationCount = 0;
    std::vector<std::function<void()>> progress;

    bool loading() const { return loadingValue; }
    bool cancelled() const { return cancelledValue; }
    uint64 loaderGeneration() const { return generation; }
    std::string loadingFilePath() const { return loadingPath; }
    std::string filepath(bool) const { return loadedPath; }
};

struct SessionData {
    DocumentData *value = nullptr;
    DocumentData *document(int) const { return value; }
};

namespace Main {
struct Session : std::enable_shared_from_this<Session> {
    SessionData value;
    SessionData &data() { return value; }
};
}

namespace base {
template <typename Type>
struct weak_ptr {
    std::weak_ptr<Type> value;
    Type *get() const {
        const auto strong = value.lock();
        return strong.get();
    }
};
}

namespace crl {
std::deque<std::function<void()>> mainQueue;
template <typename Callback>
void on_main(Callback callback) {
    mainQueue.emplace_back(std::move(callback));
}
void drain() {
    while (!mainQueue.empty()) {
        auto callback = std::move(mainQueue.front());
        mainQueue.pop_front();
        callback();
    }
}
}

bool DocumentReady(DocumentData *document, const std::string &path,
        int64 expectedSize) {
    return document->readyValue
        && !path.empty()
        && document->loadedPath == path
        && document->size == expectedSize;
}

void ReconcileDocumentDownloadAfterProgress(
        const base::weak_ptr<Main::Session> &weakSession,
        DocumentDownloadKey key,
        int64 expectedSize,
        const std::weak_ptr<ActiveDocumentDownload> &weakState);

bool FinishDocumentDownload(DocumentDownloadKey,
        const std::shared_ptr<ActiveDocumentDownload> &state,
        bool success,
        bool removePath = true) {
    if (state->finished.exchange(true)) return false;
    state->success = success;
    state->lastFinishWouldRemovePath =
        !success && removePath && !state->path.empty();
    ++state->doneCount;
    return true;
}

bool WatchExistingDocumentLoad(Main::Session *session,
        DocumentDownloadKey key,
        int64 expectedSize,
        const std::shared_ptr<ActiveDocumentDownload> &state,
        DocumentData *document) {
    if (DocumentReady(document, document->filepath(true), expectedSize)) {
        state->path = document->filepath(true);
        FinishDocumentDownload(key, state, true, false);
        return true;
    }
    if (!document->loading()) return false;
    state->path = document->loadingFilePath();
    state->ownsLoader = false;
    state->loaderGeneration = document->loaderGeneration();
    const auto weakSession = base::weak_ptr<Main::Session>{session->weak_from_this()};
    const auto weakState = std::weak_ptr<ActiveDocumentDownload>(state);
    document->progress.push_back([=] {
        ReconcileDocumentDownloadAfterProgress(
            weakSession, key, expectedSize, weakState);
    });
    return true;
}

bool BeginDocumentPathMaterialization(Main::Session *session,
        DocumentDownloadKey key,
        int64 expectedSize,
        const std::shared_ptr<ActiveDocumentDownload> &state,
        DocumentData *document) {
    if (state->finished.load()) return true;
    document->cancelledValue = false;
    document->status = 0;
    document->loadingValue = true;
    document->readyValue = false;
    document->loadingPath = "materialized-" + std::to_string(
        ++document->materializationCount);
    ++document->generation;
    state->path = document->loadingPath;
    state->loaderGeneration = document->generation;
    state->ownsLoader = true;
    state->allowSizeFallback = false;
    const auto weakSession = base::weak_ptr<Main::Session>{session->weak_from_this()};
    const auto weakState = std::weak_ptr<ActiveDocumentDownload>(state);
    document->progress.push_back([=] {
        ReconcileDocumentDownloadAfterProgress(
            weakSession, key, expectedSize, weakState);
    });
    return true;
}

void CompleteDocumentDownload(Main::Session *session,
        DocumentDownloadKey key,
        int64 expectedSize,
        const std::shared_ptr<ActiveDocumentDownload> &state) {
    if (state->finished.load()) return;
    const auto document = session->data().document(key.documentId);
    if (document->loading()) return;
    auto ownsLoader = true;
    auto path = std::string();
    auto allowSizeFallback = false;
    {
        const auto stateLock = std::lock_guard(state->mutex);
        path = state->path;
        allowSizeFallback = state->allowSizeFallback;
        ownsLoader = state->ownsLoader;
    }
    const auto ready = allowSizeFallback
        ? (document->size == expectedSize && document->loadedPath == path)
        : (document->status != FileDownloadFailed
            && !document->cancelled()
            && DocumentReady(document, path, expectedSize));
    if (!ready && !ownsLoader
        && document->status != FileDownloadFailed
        && !document->cancelled()) {
        if (BeginDocumentPathMaterialization(
                session, key, expectedSize, state, document)) {
            return;
        }
    }
    FinishDocumentDownload(key, state, ready, ownsLoader);
}

void ReconcileDocumentDownloadAfterProgress(
        const base::weak_ptr<Main::Session> &weakSession,
        DocumentDownloadKey key,
        int64 expectedSize,
        const std::weak_ptr<ActiveDocumentDownload> &weakState) {''' + reconcile + r'''}

void notifyProgress(DocumentData &document) {
    auto callbacks = std::move(document.progress);
    document.progress.clear();
    for (auto &callback : callbacks) callback();
}

int main() {
    const auto key = DocumentDownloadKey{7};

    auto replacementSession = std::make_shared<Main::Session>();
    auto replacementDocument = DocumentData();
    replacementDocument.id = key.documentId;
    replacementDocument.loadingValue = true;
    replacementDocument.generation = 4;
    replacementDocument.loadingPath = "ayu-old";
    replacementSession->value.value = &replacementDocument;
    auto replacementState = std::make_shared<ActiveDocumentDownload>();
    replacementState->path = "ayu-old";
    replacementState->loaderGeneration = 4;
    ReconcileDocumentDownloadAfterProgress(
        base::weak_ptr<Main::Session>{replacementSession}, key, 10,
        replacementState);
    replacementDocument.loadingValue = false;
    replacementDocument.generation = 5;
    replacementDocument.loadingValue = true;
    replacementDocument.loadingPath = "save-as-new";
    crl::drain();
    if (replacementState->finished || replacementState->ownsLoader
        || replacementState->loaderGeneration != 5
        || replacementState->path != "save-as-new"
        || replacementDocument.progress.size() != 1) return 1;
    replacementDocument.loadingValue = false;
    replacementDocument.readyValue = true;
    replacementDocument.loadedPath = "save-as-new";
    notifyProgress(replacementDocument);
    crl::drain();
    if (!replacementState->finished || !replacementState->success
        || replacementState->doneCount != 1) return 2;

    auto completedReplacementSession = std::make_shared<Main::Session>();
    auto completedReplacementDocument = DocumentData();
    completedReplacementDocument.id = key.documentId;
    completedReplacementDocument.loadingValue = true;
    completedReplacementDocument.generation = 4;
    completedReplacementDocument.loadingPath = "ayu-old";
    completedReplacementSession->value.value = &completedReplacementDocument;
    auto completedReplacementState = std::make_shared<ActiveDocumentDownload>();
    completedReplacementState->path = "ayu-old";
    completedReplacementState->loaderGeneration = 4;
    ReconcileDocumentDownloadAfterProgress(
        base::weak_ptr<Main::Session>{completedReplacementSession}, key, 10,
        completedReplacementState);
    completedReplacementDocument.loadingValue = false;
    completedReplacementDocument.generation = 5;
    completedReplacementDocument.loadingPath.clear();
    crl::drain();
    if (completedReplacementState->finished
        || completedReplacementDocument.materializationCount != 1
        || !completedReplacementDocument.loadingValue
        || completedReplacementDocument.generation != 6
        || !completedReplacementState->ownsLoader
        || completedReplacementState->loaderGeneration != 6) return 7;
    completedReplacementDocument.loadingValue = false;
    completedReplacementDocument.readyValue = true;
    completedReplacementDocument.loadedPath =
        completedReplacementDocument.loadingPath;
    notifyProgress(completedReplacementDocument);
    crl::drain();
    if (!completedReplacementState->finished
        || !completedReplacementState->success
        || completedReplacementState->doneCount != 1
        || completedReplacementState->lastFinishWouldRemovePath) return 8;

    auto failedReplacementSession = std::make_shared<Main::Session>();
    auto failedReplacementDocument = DocumentData();
    failedReplacementDocument.id = key.documentId;
    failedReplacementDocument.loadingValue = true;
    failedReplacementDocument.generation = 8;
    failedReplacementDocument.loadingPath = "ayu-old";
    failedReplacementDocument.loadedPath = "replacement-partial";
    failedReplacementSession->value.value = &failedReplacementDocument;
    auto failedReplacementState = std::make_shared<ActiveDocumentDownload>();
    failedReplacementState->path = "ayu-old";
    failedReplacementState->loaderGeneration = 8;
    ReconcileDocumentDownloadAfterProgress(
        base::weak_ptr<Main::Session>{failedReplacementSession}, key, 10,
        failedReplacementState);
    failedReplacementDocument.loadingValue = false;
    failedReplacementDocument.generation = 9;
    failedReplacementDocument.status = FileDownloadFailed;
    crl::drain();
    if (!failedReplacementState->finished || failedReplacementState->success
        || failedReplacementDocument.materializationCount != 0
        || failedReplacementState->doneCount != 1
        || failedReplacementState->lastFinishWouldRemovePath) return 9;

    auto retrySession = std::make_shared<Main::Session>();
    auto retryDocument = DocumentData();
    retryDocument.id = key.documentId;
    retryDocument.loadingValue = true;
    retryDocument.generation = 9;
    retryDocument.loadingPath = "ayu-cancelled";
    retrySession->value.value = &retryDocument;
    auto retryState = std::make_shared<ActiveDocumentDownload>();
    retryState->path = "ayu-cancelled";
    retryState->loaderGeneration = 9;
    ReconcileDocumentDownloadAfterProgress(
        base::weak_ptr<Main::Session>{retrySession}, key, 10, retryState);
    retryDocument.loadingValue = false;
    retryDocument.cancelledValue = true;
    crl::drain();
    if (retryState->finished || retryDocument.materializationCount != 1
        || !retryDocument.loadingValue || retryDocument.generation != 10
        || !retryState->ownsLoader) return 3;
    retryDocument.loadingValue = false;
    retryDocument.readyValue = true;
    retryDocument.loadedPath = retryDocument.loadingPath;
    notifyProgress(retryDocument);
    crl::drain();
    if (!retryState->finished || !retryState->success
        || retryState->doneCount != 1) return 4;

    auto cancelledSession = std::make_shared<Main::Session>();
    auto cancelledDocument = DocumentData();
    cancelledDocument.id = key.documentId;
    cancelledDocument.loadingValue = true;
    cancelledDocument.generation = 2;
    cancelledSession->value.value = &cancelledDocument;
    auto cancelledState = std::make_shared<ActiveDocumentDownload>();
    cancelledState->loaderGeneration = 2;
    cancelledState->finished = true;
    ReconcileDocumentDownloadAfterProgress(
        base::weak_ptr<Main::Session>{cancelledSession}, key, 10,
        cancelledState);
    cancelledDocument.loadingValue = false;
    cancelledDocument.cancelledValue = true;
    crl::drain();
    if (cancelledDocument.materializationCount != 0
        || cancelledState->doneCount != 0) return 5;

    auto failedSession = std::make_shared<Main::Session>();
    auto failedDocument = DocumentData();
    failedDocument.id = key.documentId;
    failedDocument.status = FileDownloadFailed;
    failedSession->value.value = &failedDocument;
    auto failedState = std::make_shared<ActiveDocumentDownload>();
    failedState->loaderGeneration = 3;
    ReconcileDocumentDownloadAfterProgress(
        base::weak_ptr<Main::Session>{failedSession}, key, 10, failedState);
    crl::drain();
    if (!failedState->finished || failedState->success
        || failedDocument.materializationCount != 0
        || failedState->doneCount != 1) return 6;
    return 0;
}
'''
        compile_and_run(cpp)

    def test_cancellation_reads_materialization_generation_on_main_thread(self):
        source = (SOURCE / "ayu/features/forward/ayu_sync_download.cpp").read_text()
        cancel = block_body(source, "void CancelDocumentDownload(")
        main_thread = block_body(cancel, "crl::on_main_sync([=] {")
        self.assertLess(
            cancel.index("crl::on_main_sync([=] {"),
            cancel.index("loaderGeneration = state->loaderGeneration"),
        )
        self.assertIn("ownsLoader = state->ownsLoader", main_thread)
        self.assertIn("loaderGeneration = state->loaderGeneration", main_thread)
        self.assertRegex(
            main_thread,
            r"cancelSharedDownloadIfUnused\(\s*loaderGeneration\)",
        )

    def test_cancelled_owner_keeps_state_for_another_consumer(self):
        source = (SOURCE / "ayu/features/forward/ayu_sync_download.cpp").read_text()
        abandon = block_body(
            source,
            "bool AbandonCancelledDocumentDownloadIfUnshared(\n\t\tDocumentDownloadKey key,",
        )
        cpp = r'''
#include <atomic>
#include <cassert>
#include <cstdint>
#include <functional>
#include <map>
#include <memory>
#include <mutex>
using Fn = std::function<bool()>;
struct DocumentDownloadKey {
    uint64_t sessionUniqueId = 0;
    uint64_t documentId = 0;
    friend auto operator<=>(DocumentDownloadKey, DocumentDownloadKey) = default;
};
struct TimedCountDownLatch {
    void countDown() { ++calls; }
    int calls = 0;
};
struct ActiveDocumentDownload {
    int consumers = 1;
    std::atomic_bool finished = false;
    TimedCountDownLatch done;
};
using Downloads = std::map<DocumentDownloadKey,
    std::shared_ptr<ActiveDocumentDownload>>;
std::mutex &DocumentDownloadsMutex() {
    static auto mutex = std::mutex();
    return mutex;
}
Downloads &DocumentDownloads() {
    static auto downloads = Downloads();
    return downloads;
}
bool AbandonCancelledDocumentDownloadIfUnshared(
    DocumentDownloadKey key,
    const std::shared_ptr<ActiveDocumentDownload> &state,
    const Fn &cancelled) {''' + abandon + r'''
}
int main() {
    const auto cancelled = [] { return true; };
    const auto sharedKey = DocumentDownloadKey{ 1, 10 };
    auto shared = std::make_shared<ActiveDocumentDownload>();
    shared->consumers = 2;
    DocumentDownloads().emplace(sharedKey, shared);
    if (AbandonCancelledDocumentDownloadIfUnshared(
            sharedKey, shared, cancelled)
        || shared->finished.load()
        || shared->done.calls != 0
        || DocumentDownloads().at(sharedKey) != shared) {
        return 1;
    }

    const auto soleKey = DocumentDownloadKey{ 1, 11 };
    auto sole = std::make_shared<ActiveDocumentDownload>();
    DocumentDownloads().emplace(soleKey, sole);
    if (!AbandonCancelledDocumentDownloadIfUnshared(
            soleKey, sole, cancelled)
        || !sole->finished.load()
        || sole->done.calls != 1
        || DocumentDownloads().contains(soleKey)) {
        return 2;
    }
    return 0;
}
'''
        compile_and_run(cpp)

    def test_photo_forward_preserves_same_named_download_file(self):
        source = (SOURCE / "ayu/features/forward/ayu_sync_download.cpp").read_text()
        file_path = block_body(source, "QString filePath(")
        cpp = r'''#include <cstddef>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <string>
struct QString {
    QString() = default;
    QString(std::string value) : value(std::move(value)) {}
    static QString number(int value) { return QString(std::to_string(value)); }
    bool isEmpty() const { return value.empty(); }
    const std::string &toStdString() const { return value; }
    std::string value;
};
QString operator+(QString left, QString right) {
    return QString(left.value + right.value);
}
QString operator""_q(const char16_t *value, std::size_t length) {
    auto result = std::string();
    for (auto i = 0U; i != length; ++i) result.push_back(char(value[i]));
    return QString(result);
}
struct QDir {
    explicit QDir(QString path) : path(std::move(path)) {}
    QString filePath(QString name) const {
        return QString((std::filesystem::path(path.value)
            / name.value).string());
    }
    QString path;
};
struct Local {
    QString directory;
    QString tempDirectory() const { return directory; }
};
struct Session {
    QString downloads;
    Local storage;
    Local &local() { return storage; }
};
struct PhotoData {
    int id = 0;
    int dc = 0;
    int getDC() const { return dc; }
};
QString pathForSave(Session *session) { return session->downloads; }
QString filePath(Session *session, PhotoData *photo) {''' + file_path + r'''}
int main(int, char **argv) {
    const auto root = std::filesystem::path(argv[1]);
    const auto downloads = root / "Downloads";
    const auto appTemp = root / "account-temp";
    std::filesystem::create_directories(downloads);
    std::filesystem::create_directories(appTemp);
    const auto sentinel = downloads / "42_7.jpg";
    {
        auto file = std::ofstream(sentinel);
        file << "keep-user-file";
    }
    auto session = Session{
        QString(downloads.string()),
        Local{ QString(appTemp.string()) },
    };
    auto photo = PhotoData{ 7, 42 };
    const auto destination = filePath(&session, &photo);
    std::filesystem::create_directories(
        std::filesystem::path(destination.value).parent_path());
    {
        auto file = std::ofstream(destination.value);
        file << "forwarded-photo";
    }
    auto input = std::ifstream(sentinel);
    const auto contents = std::string(
        std::istreambuf_iterator<char>(input),
        std::istreambuf_iterator<char>());
    const auto expected = (appTemp / "ayu-forward" / "42_7.jpg").string();
    return contents == "keep-user-file"
        && destination.value == expected
        ? 0
        : 1;
}
'''
        with test_temporary_directory(
            prefix="ayu-forward-photo-path-",
        ) as directory:
            directory = pathlib.Path(directory)
            test_source = directory / "test.cpp"
            binary = directory / "test"
            test_source.write_text(cpp)
            compiler = shlex.split(os.environ.get("CXX", "c++"))
            subprocess.run(compiler + [
                "-std=c++20", "-O0", "-g", "-Wall", "-Wextra", "-pedantic",
                str(test_source), "-o", str(binary),
            ], check=True, timeout=60)
            result = subprocess.run(
                [str(binary), str(directory)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode:
                raise AssertionError(
                    f"exit {result.returncode}\n{result.stdout}{result.stderr}")

    def test_photo_forward_retries_a_failed_full_preload(self):
        source = (SOURCE / "ayu/features/forward/ayu_sync_download.cpp").read_text()
        load_photo = block_body(source, "void loadPhotoSync(")
        clear_failed = load_photo.index(
            "data->clearFailed(Data::PhotoSize::Large);"
        )
        begin_preload = load_photo.index(
            "std::make_shared<Data::PhotoPreload>("
        )

        self.assertLess(clear_failed, begin_preload)
        self.assertIn("Data::PhotoPreload::Completion::Full", load_photo)

    def test_cancelled_photo_forward_releases_only_its_preload_demand(self):
        source = (SOURCE / "ayu/features/forward/ayu_sync_download.cpp").read_text()
        load_photo = block_body(source, "void loadPhotoSync(")
        self.assertIn("std::make_shared<Data::PhotoPreload>", load_photo)
        self.assertIn("Data::PhotoPreload::Completion::Full", load_photo)
        self.assertIn("data->owner().photoLoadProgress()", load_photo)
        self.assertIn("photoPreloadFinished->store(true", load_photo)
        self.assertIn("preload.reset();", load_photo)
        self.assertIn("cancelled && cancelled()", load_photo)
        self.assertNotIn("view->wanted(", load_photo)

        photo = (SOURCE / "data/data_photo.cpp").read_text()
        release = block_body(photo, "void PhotoData::releasePreload(")
        cpp = r'''#include <cassert>
#include <cstdlib>
#include <memory>
#include <utility>
#include <vector>
#define Expects(...) assert(__VA_ARGS__)
constexpr auto LoadFromCloudOrLocal = 0;
namespace base {
template <typename T>
T take(T &value) {
    return std::exchange(value, T());
}
}
enum class PhotoSize { Large };
int PhotoSizeIndex(PhotoSize) { return 0; }
struct Loader {
    bool finished() const { return isFinished; }
    int fileOrigin() const { return 42; }
    void cancel() { isFinished = true; }
    bool isFinished = false;
};
struct Image {
    std::unique_ptr<Loader> loader = std::make_unique<Loader>();
};
struct PhotoData {
    void releasePreload(bool requireFull = false) {''' + release + r'''}
    bool cancelled() const { return wasCancelled; }
    bool loading(PhotoSize) const { return isLoading; }
    void loadInternal(PhotoSize, int, int, bool, bool, bool) {}
    void cancel(bool) { wasCancelled = true; }
    int _preloadRequests = 1;
    int _fullPreloadRequests = 1;
    bool _regularLargeLoad = false;
    bool wasCancelled = false;
    bool isLoading = true;
    std::vector<Image> _images = std::vector<Image>(1);
};
int main() {
    auto auxiliaryOnly = PhotoData();
    auxiliaryOnly.releasePreload(true);
    if (!auxiliaryOnly.wasCancelled || auxiliaryOnly._preloadRequests != 0
        || auxiliaryOnly._fullPreloadRequests != 0) {
        return 1;
    }
    auto alsoNeededByUi = PhotoData();
    alsoNeededByUi._regularLargeLoad = true;
    alsoNeededByUi.releasePreload(true);
    if (alsoNeededByUi.wasCancelled || alsoNeededByUi._preloadRequests != 0
        || alsoNeededByUi._fullPreloadRequests != 0) {
        return 2;
    }
    return 0;
}
        '''
        compile_and_run(cpp)


    def test_waveform_codec_uses_unaligned_access_helpers(self):
        document = (SOURCE / "data/data_document.cpp").read_text()
        decode = block_body(
            document,
            "VoiceWaveform documentWaveformDecode(",
        )
        encode = block_body(
            document,
            "QByteArray documentWaveformEncode5bit(",
        )
        self.assertIn("#include <QtCore/QtEndian>", document)
        self.assertNotIn("reinterpret_cast<const uint16*>", decode)
        self.assertNotIn("reinterpret_cast<uint16*>", encode)
        self.assertIn("qFromUnaligned<uint16>(bitsData + byteIndex)", decode)
        self.assertIn("qFromUnaligned<uint16>(bitsData + lastByteIndex)", decode)
        self.assertIn("qFromUnaligned<uint16>(bitsData + byteIndex)", encode)
        self.assertIn("qToUnaligned(uint16(previous | value)", encode)

        cpp = r'''#include <algorithm>
#include <cstdint>
#include <cstring>
#include <vector>
using uint16 = std::uint16_t;
using uchar = unsigned char;
template <typename Type>
Type qFromUnaligned(const char *source) {
    auto result = Type();
    std::memcpy(&result, source, sizeof(result));
    return result;
}
template <typename Type>
void qToUnaligned(Type value, char *destination) {
    std::memcpy(destination, &value, sizeof(value));
}
using VoiceWaveform = std::vector<char>;
class QByteArray {
public:
    QByteArray(int size, char value)
    : _size(size), _bytes(static_cast<std::size_t>(size) + 2, value) {
    }
    explicit QByteArray(const std::vector<char> &bytes)
    : QByteArray(static_cast<int>(bytes.size()), 0) {
        std::copy(bytes.begin(), bytes.end(), data());
    }
    int size() const { return _size; }
    const char *constData() const { return _bytes.data() + 1; }
    char *data() { return _bytes.data() + 1; }
    void resize(int size) {
        _size = size;
        _bytes.resize(static_cast<std::size_t>(size) + 2);
    }
private:
    int _size = 0;
    std::vector<char> _bytes;
};
''' + "VoiceWaveform documentWaveformDecode(const QByteArray &encoded5bit) {" + decode + "}\n" + "QByteArray documentWaveformEncode5bit(const VoiceWaveform &waveform) {" + encode + r'''}
int main() {
    for (auto size = 0; size != 257; ++size) {
        auto expected = VoiceWaveform();
        for (auto i = 0; i != size; ++i) {
            expected.push_back(static_cast<char>((i * 17) & 0x1F));
        }
        const auto encoded = documentWaveformEncode5bit(expected);
        const auto decoded = documentWaveformDecode(encoded);
        const auto expectedDecodedSize = static_cast<std::size_t>(
            (encoded.size() * 8) / 5);
        if (decoded.size() != expectedDecodedSize) {
            return 1;
        }
        if (!std::equal(expected.begin(), expected.end(), decoded.begin())) {
            return 2;
        }
        for (auto i = expected.size(); i != decoded.size(); ++i) {
            if (decoded[i] != 0) {
                return 3;
            }
        }
    }
    return 0;
}
'''
        compile_and_run(cpp)


if __name__ == "__main__":
    unittest.main()
