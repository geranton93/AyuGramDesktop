// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#pragma once

#include "apiwrap.h"
#include "base/flat_map.h"
#include "base/random.h"
#include "base/weak_ptr.h"
#include "data/data_document.h"
#include "data/data_media_types.h"
#include "data/data_photo.h"
#include "history/history_item.h"
#include "history/history.h"
#include "storage/file_download.h"
#include "storage/file_upload.h"
#include "storage/storage_account.h"
#include "ui/chat/attach/attach_prepare.h"

#include <optional>

namespace Iv {
struct RichPage;
} // namespace Iv

namespace AyuSync {

struct PhotoSnapshot {
	PhotoId id = 0;
	int64 size = 0;
	QString path;
};

struct DocumentSnapshot {
	DocumentId id = 0;
	int64 size = 0;
	QString name;
	crl::time duration = 0;
	bool sticker = false;
	bool voice = false;
	bool round = false;
	bool video = false;
};

using DocumentPaths = base::flat_map<DocumentId, QString>;

struct DownloadItem {
	FullMsgId fullId;
	std::optional<PhotoSnapshot> photo;
	std::optional<DocumentSnapshot> document;
};

struct UploadedFile
{
	PhotoId photoId = 0;
	DocumentId documentId = 0;
};

QString pathForSave(not_null<Main::Session*> session);
QString documentFileName(not_null<DocumentData*> document);
QString filePath(not_null<Main::Session*> session, not_null<PhotoData*> photo);
qint64 fileSize(const QString &path);
[[nodiscard]] bool isValidPhotoSnapshot(const PhotoSnapshot &photo);
[[nodiscard]] bool isValidDocumentSnapshot(
	const DocumentSnapshot &document);
[[nodiscard]] PhotoSnapshot snapshotPhoto(
	not_null<Main::Session*> session,
	not_null<PhotoData*> photo);
[[nodiscard]] DocumentSnapshot snapshotDocument(not_null<DocumentData*> document);
[[nodiscard]] DocumentPaths loadDocuments(
	not_null<Main::Session*> session,
	const std::vector<DownloadItem> &items,
	const Fn<bool()> &cancelled);
void sendMessageSync(
	not_null<Main::Session*> session,
	Api::MessageToSend &&message,
	base::weak_ptr<History> targetHistory);

void sendDocumentSync(not_null<Main::Session*> session,
					  Ui::PreparedGroup &group,
					  SendMediaType type,
					  TextWithTags &&caption,
					  const Api::SendAction &action,
					  base::weak_ptr<History> targetHistory);

void sendStickerSync(not_null<Main::Session*> session,
						 Api::MessageToSend &&message,
						 DocumentId documentId,
						 base::weak_ptr<History> targetHistory);
void waitForMsgSync(
	not_null<Main::Session*> session,
	base::weak_ptr<History> targetHistory);
void loadPhotoSync(
	not_null<Main::Session*> session,
	const PhotoSnapshot &photo,
	Data::FileOrigin origin,
	const Fn<bool()> &cancelled);
[[nodiscard]] QString loadDocumentSync(
	not_null<Main::Session*> session,
	const DocumentSnapshot &document,
	Data::FileOrigin origin,
	const Fn<bool()> &cancelled);
void forwardMessagesSync(not_null<Main::Session*> session,
						 const MessageIdsList &itemIds,
						 const ApiWrap::SendAction &action,
						 Data::ForwardOptions options,
						 base::weak_ptr<History> targetHistory);
void sendVoiceSync(not_null<Main::Session*> session,
				   const QByteArray &data,
				   int64_t duration,
				   bool video,
				   Api::MessageToSend &&message,
				   base::weak_ptr<History> targetHistory);

UploadedFile uploadFileSync(not_null<Main::Session*> session,
							PeerId peerId,
							const QString &path,
							SendMediaType type,
							bool forceFile,
							const QString &displayName = {},
							const Fn<bool()> &cancelled = {});

std::shared_ptr<const Iv::RichPage> loadFullRichPageSync(
	not_null<Main::Session*> session,
	FullMsgId itemId);

bool sendRichMessageSync(not_null<Main::Session*> session,
						 const MTPInputRichMessage &richMessage,
						 const Api::SendAction &action,
						 base::weak_ptr<History> targetHistory);
} // namespace AyuSync
